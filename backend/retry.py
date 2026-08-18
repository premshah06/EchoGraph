"""
Retry handler for LLM and external API calls.

Three error classes with different strategies:
  Transient  — rate limits, timeouts, 5xx    → retry with jitter backoff
  Parse      — malformed LLM output          → retry immediately (different seed)
  Fatal      — auth errors, bad requests     → fail fast, no retry

Also provides a proactive token-bucket rate limiter (throttles calls before
they're sent, rather than only reacting to a 429 after the fact) and a
circuit breaker (stops hammering a downed OpenAI API after repeated failures,
failing fast with a clear error instead of retrying into a wall). Neither
silently falls back to DemoLLMClient's scripted responses — a real ingestion
that quietly started returning fabricated content without the caller knowing
would be actively misleading, so the circuit breaker's job is to fail loudly,
not fake success.

Usage
-----
from backend.retry import with_retry

result = with_retry(llm_client.invoke, prompt, agent="philosopher")
"""

from __future__ import annotations

import inspect
import logging
import random
import threading
import time
from typing import Any, Callable, Optional, Tuple

logger = logging.getLogger(__name__)

# Maximum wall-clock seconds any single LLM call may take before we abort.
CALL_TIMEOUT_SECONDS = 30

# Backoff config: wait = base * (2 ** attempt) + jitter
BACKOFF_BASE    = 0.4   # seconds
BACKOFF_MAX     = 8.0   # seconds ceiling
JITTER_RANGE    = 0.3   # ± random fraction of the wait

# Circuit breaker: open after this many consecutive failures, half-open
# (try one real call) after this many seconds of staying open.
CIRCUIT_FAILURE_THRESHOLD = 5
CIRCUIT_RESET_SECONDS = 30.0

# Token bucket: shared across all agents, since OpenAI enforces rate limits
# per API key, not per calling code — a per-agent bucket wouldn't reflect
# the real constraint. Refill continuously at rate_per_second.
DEFAULT_BUCKET_CAPACITY = 10.0
DEFAULT_REFILL_PER_SECOND = 3.0


class CircuitOpenError(Exception):
    """Raised immediately (no retry) when the circuit breaker is open."""


class CircuitBreaker:
    """
    Tracks consecutive LLM call failures. After CIRCUIT_FAILURE_THRESHOLD
    consecutive failures, the circuit opens: further calls raise
    CircuitOpenError immediately without attempting the network call, until
    CIRCUIT_RESET_SECONDS have passed, at which point one call is allowed
    through (half-open) to test recovery. A success closes the circuit and
    resets the failure count; a half-open failure re-opens it.
    """

    def __init__(self, failure_threshold: int = CIRCUIT_FAILURE_THRESHOLD, reset_seconds: float = CIRCUIT_RESET_SECONDS):
        self.failure_threshold = failure_threshold
        self.reset_seconds = reset_seconds
        self._lock = threading.Lock()
        self._consecutive_failures = 0
        self._opened_at: Optional[float] = None

    @property
    def is_open(self) -> bool:
        with self._lock:
            return self._state_locked() == "open"

    def _state_locked(self) -> str:
        """Caller must hold self._lock."""
        if self._opened_at is None:
            return "closed"
        if time.time() - self._opened_at >= self.reset_seconds:
            return "half_open"
        return "open"

    def before_call(self) -> None:
        """Raise CircuitOpenError if the circuit is open; no-op otherwise."""
        with self._lock:
            state = self._state_locked()
            if state == "open":
                remaining = self.reset_seconds - (time.time() - self._opened_at)
                raise CircuitOpenError(
                    f"Circuit breaker open after {self._consecutive_failures} consecutive failures; "
                    f"retry in {max(0.0, remaining):.0f}s"
                )
            # half_open: allow this one call through as a recovery probe.

    def record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.failure_threshold:
                self._opened_at = time.time()
                logger.error(
                    "circuit_breaker.open after %d consecutive failures",
                    self._consecutive_failures,
                )

    def status(self) -> dict:
        with self._lock:
            return {
                "state": self._state_locked(),
                "consecutive_failures": self._consecutive_failures,
            }


class TokenBucketRateLimiter:
    """
    Classic token bucket: holds up to `capacity` tokens, refilling
    continuously at `refill_per_second`. Each call consumes one token;
    if none are available, blocks (sleeps) until one refills rather than
    sending the request and hoping OpenAI doesn't 429 it.
    """

    def __init__(self, capacity: float = DEFAULT_BUCKET_CAPACITY, refill_per_second: float = DEFAULT_REFILL_PER_SECOND):
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self._lock = threading.Lock()
        self._tokens = capacity
        self._last_refill = time.time()

    def _refill_locked(self) -> None:
        now = time.time()
        elapsed = now - self._last_refill
        self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_per_second)
        self._last_refill = now

    def acquire(self) -> float:
        """Block until a token is available; returns seconds waited."""
        waited = 0.0
        while True:
            with self._lock:
                self._refill_locked()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return waited
                deficit = 1.0 - self._tokens
                sleep_for = deficit / self.refill_per_second

            time.sleep(sleep_for)
            waited += sleep_for


# Module-level singletons: the whole process shares one bucket/breaker, since
# they model a real shared constraint (one OpenAI API key's rate limit and
# one upstream service's health), not per-call or per-agent state.
default_rate_limiter = TokenBucketRateLimiter()
default_circuit_breaker = CircuitBreaker()


def _classify(exc: Exception) -> str:
    """
    Return 'transient', 'parse', or 'fatal' for a given exception.

    Transient → safe to retry with backoff
    Parse     → safe to retry immediately (LLM output was malformed)
    Fatal     → do not retry (auth, validation, programming error)
    """
    msg = str(exc).lower()
    cls = type(exc).__name__.lower()

    # Auth / configuration — never retry
    if any(k in msg for k in ("authentication", "invalid api key", "permission", "forbidden")):
        return "fatal"

    # Bad request we sent — retrying won't help
    if "invalid request" in msg or "400" in msg:
        return "fatal"

    # Rate limits and server errors — retry with backoff
    if any(k in msg for k in ("rate limit", "429", "too many requests",
                               "502", "503", "504", "overloaded",
                               "timeout", "timed out", "connection")):
        return "transient"

    # OpenAI library specific
    if any(k in cls for k in ("ratelimit", "apitimeout", "apiconnection",
                               "serviceunavailable", "internalservererror")):
        return "transient"

    # JSON / parse errors from our own parsers
    if any(k in cls for k in ("json", "decode", "parse", "value")):
        return "parse"
    if any(k in msg for k in ("json", "parse", "expected", "unexpected token")):
        return "parse"

    # Unknown — treat as transient (safe default)
    return "transient"


def _backoff_wait(attempt: int) -> float:
    """Exponential backoff with ±30% jitter, capped at BACKOFF_MAX."""
    base_wait = min(BACKOFF_BASE * (2 ** attempt), BACKOFF_MAX)
    jitter = base_wait * JITTER_RANGE * (2 * random.random() - 1)
    return max(0.05, base_wait + jitter)


def with_retry(
    fn: Callable,
    *args: Any,
    agent: str = "unknown",
    max_attempts: int = 3,
    parse_max_attempts: int = 2,
    timeout: Optional[float] = CALL_TIMEOUT_SECONDS,
    rate_limiter: Optional[TokenBucketRateLimiter] = None,
    circuit_breaker: Optional[CircuitBreaker] = None,
    **kwargs: Any,
) -> Any:
    """
    Call fn(*args, **kwargs) with error-type-aware retry logic, and
    optionally proactive rate limiting / circuit breaking.

    rate_limiter and circuit_breaker default to None (disabled) so this
    function's behavior is unchanged for existing callers and tests — pass
    backend.retry.default_rate_limiter / default_circuit_breaker explicitly
    to opt in to the shared, process-wide versions (see get_protected_llm_client
    for the wrapper agents actually use).

    Parameters
    ----------
    fn              : The callable to wrap (e.g. llm_client.invoke)
    *args           : Positional args forwarded to fn
    agent           : Agent name for structured log context. Also forwarded to
                      fn as a keyword arg (every real LLM client accepts
                      `agent`, e.g. OptimizedLLMClient.invoke uses it for
                      cost/model-routing bookkeeping) — UNLESS `agent` is
                      already supplied positionally in *args, in which case
                      forwarding it again as a kwarg would raise
                      "got multiple values for argument 'agent'" (this is the
                      case for invoke_streaming(prompt, agent, on_token)).
    max_attempts    : Max retries for transient errors
    parse_max_attempts : Max retries for parse errors
    timeout         : Per-call wall-clock timeout in seconds (None = no limit)
    rate_limiter    : Token bucket consulted before every attempt; None disables (default)
    circuit_breaker : Breaker consulted before every attempt; None disables (default)
    **kwargs        : Keyword args forwarded to fn
    """
    if circuit_breaker is not None:
        circuit_breaker.before_call()

    # Forward agent to fn so cost/routing bookkeeping (e.g. OptimizedLLMClient)
    # sees the real caller instead of the "unknown" default — but only when fn
    # actually accepts an `agent` parameter, and only when it isn't already
    # supplied positionally (Python raises "got multiple values for argument"
    # if both are supplied; see invoke_streaming(prompt, agent, on_token)).
    # fn may be an arbitrary callable (tests pass plain closures that accept
    # no `agent` at all), so both checks are required, not just the second.
    call_kwargs = dict(kwargs)
    if "agent" not in call_kwargs:
        try:
            params = inspect.signature(fn).parameters
            accepts_agent = "agent" in params or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
            )
            bound = inspect.signature(fn).bind_partial(*args)
            already_positional = "agent" in bound.arguments
        except (TypeError, ValueError):
            accepts_agent = False
            already_positional = False
        if accepts_agent and not already_positional:
            call_kwargs["agent"] = agent

    transient_attempts = 0
    parse_attempts     = 0
    last_exc: Optional[Exception] = None

    for overall_attempt in range(max_attempts + parse_max_attempts):
        try:
            if rate_limiter is not None:
                waited = rate_limiter.acquire()
                if waited > 0:
                    logger.info("rate_limit.waited agent=%s seconds=%.2f", agent, waited)

            if timeout is not None:
                result = _call_with_timeout(fn, args, call_kwargs, timeout)
            else:
                result = fn(*args, **call_kwargs)

            if circuit_breaker is not None:
                circuit_breaker.record_success()

            if overall_attempt > 0:
                logger.info(
                    "retry.success agent=%s attempt=%d",
                    agent, overall_attempt + 1,
                )
            return result

        except Exception as exc:
            last_exc = exc
            error_class = _classify(exc)

            logger.warning(
                "retry.%s agent=%s attempt=%d error=%s: %s",
                error_class, agent, overall_attempt + 1,
                type(exc).__name__, str(exc)[:200],
            )

            if error_class == "fatal":
                logger.error("retry.fatal agent=%s — not retrying: %s", agent, exc)
                raise

            if error_class == "transient":
                transient_attempts += 1
                if transient_attempts >= max_attempts:
                    logger.error(
                        "retry.exhausted agent=%s after %d transient attempts",
                        agent, transient_attempts,
                    )
                    if circuit_breaker is not None:
                        circuit_breaker.record_failure()
                    raise

                wait = _backoff_wait(transient_attempts - 1)
                logger.info("retry.wait agent=%s sleeping=%.2fs", agent, wait)
                time.sleep(wait)

            elif error_class == "parse":
                parse_attempts += 1
                if parse_attempts >= parse_max_attempts:
                    logger.error(
                        "retry.exhausted agent=%s after %d parse attempts",
                        agent, parse_attempts,
                    )
                    raise
                # Parse errors: retry immediately — different random seed may fix it.
                logger.info("retry.parse agent=%s retrying immediately", agent)

    # Should not reach here, but safety net
    raise last_exc or RuntimeError(f"with_retry exhausted all attempts for agent={agent}")


def _call_with_timeout(
    fn: Callable,
    args: Tuple,
    kwargs: dict,
    timeout: float,
) -> Any:
    """
    Run fn(*args, **kwargs) in a thread with a wall-clock timeout.

    Raises TimeoutError if the call does not complete within `timeout` seconds.
    Uses a daemon thread so it doesn't block process shutdown.
    """
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(fn, *args, **kwargs)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            raise TimeoutError(
                f"LLM call exceeded {timeout}s timeout"
            )
