"""
Retry handler for LLM and external API calls.

Three error classes with different strategies:
  Transient  — rate limits, timeouts, 5xx    → retry with jitter backoff
  Parse      — malformed LLM output          → retry immediately (different seed)
  Fatal      — auth errors, bad requests     → fail fast, no retry

Usage
-----
from backend.retry import with_retry

result = with_retry(llm_client.invoke, prompt, agent="philosopher")
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any, Callable, Optional, Tuple

logger = logging.getLogger(__name__)

# Maximum wall-clock seconds any single LLM call may take before we abort.
CALL_TIMEOUT_SECONDS = 30

# Backoff config: wait = base * (2 ** attempt) + jitter
BACKOFF_BASE    = 0.4   # seconds
BACKOFF_MAX     = 8.0   # seconds ceiling
JITTER_RANGE    = 0.3   # ± random fraction of the wait


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
    **kwargs: Any,
) -> Any:
    """
    Call fn(*args, **kwargs) with error-type-aware retry logic.

    Parameters
    ----------
    fn              : The callable to wrap (e.g. llm_client.invoke)
    *args           : Positional args forwarded to fn
    agent           : Agent name for structured log context
    max_attempts    : Max retries for transient errors
    parse_max_attempts : Max retries for parse errors
    timeout         : Per-call wall-clock timeout in seconds (None = no limit)
    **kwargs        : Keyword args forwarded to fn
    """
    transient_attempts = 0
    parse_attempts     = 0
    last_exc: Optional[Exception] = None

    for overall_attempt in range(max_attempts + parse_max_attempts):
        try:
            if timeout is not None:
                result = _call_with_timeout(fn, args, kwargs, timeout)
            else:
                result = fn(*args, **kwargs)

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
