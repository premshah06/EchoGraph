"""Unit tests for the retry handler."""

from __future__ import annotations

import time

import pytest

from backend.retry import (
    CircuitBreaker,
    CircuitOpenError,
    TokenBucketRateLimiter,
    _backoff_wait,
    _classify,
    with_retry,
)


# ── _classify ──────────────────────────────────────────────────────────────

class TestClassify:
    def test_rate_limit_is_transient(self):
        assert _classify(Exception("rate limit exceeded")) == "transient"

    def test_429_is_transient(self):
        assert _classify(Exception("429 Too Many Requests")) == "transient"

    def test_timeout_is_transient(self):
        assert _classify(Exception("request timed out")) == "transient"

    def test_connection_error_is_transient(self):
        assert _classify(Exception("connection reset by peer")) == "transient"

    def test_503_is_transient(self):
        assert _classify(Exception("503 Service Unavailable")) == "transient"

    def test_auth_error_is_fatal(self):
        assert _classify(Exception("authentication failed")) == "fatal"

    def test_invalid_api_key_is_fatal(self):
        assert _classify(Exception("invalid api key")) == "fatal"

    def test_400_bad_request_is_fatal(self):
        assert _classify(Exception("400 invalid request")) == "fatal"

    def test_json_decode_error_is_parse(self):
        import json
        try:
            import json
            json.loads("not json {")
        except json.JSONDecodeError as exc:
            assert _classify(exc) == "parse"

    def test_value_error_is_parse(self):
        assert _classify(ValueError("could not parse response")) == "parse"

    def test_unknown_error_is_transient(self):
        assert _classify(Exception("something weird happened")) == "transient"


# ── _backoff_wait ──────────────────────────────────────────────────────────

class TestBackoffWait:
    def test_positive(self):
        assert _backoff_wait(0) > 0

    def test_increases_with_attempt(self):
        assert _backoff_wait(1) > _backoff_wait(0)
        assert _backoff_wait(2) > _backoff_wait(1)

    def test_capped_at_max(self):
        from backend.retry import BACKOFF_MAX, JITTER_RANGE
        # Even with max jitter the wait should not far exceed BACKOFF_MAX
        for attempt in range(10):
            w = _backoff_wait(attempt)
            assert w <= BACKOFF_MAX * (1 + JITTER_RANGE) + 0.1


# ── with_retry ─────────────────────────────────────────────────────────────

class TestWithRetry:
    def test_success_on_first_call(self):
        calls = []
        def fn(x):
            calls.append(x)
            return "ok"
        result = with_retry(fn, 42, agent="test", timeout=None)
        assert result == "ok"
        assert len(calls) == 1

    def test_retries_on_transient_error(self):
        attempts = []
        def fn():
            attempts.append(1)
            if len(attempts) < 3:
                raise Exception("503 service unavailable")
            return "recovered"
        result = with_retry(fn, agent="test", max_attempts=3, timeout=None)
        assert result == "recovered"
        assert len(attempts) == 3

    def test_raises_after_max_transient_attempts(self):
        def fn():
            raise Exception("rate limit exceeded")
        with pytest.raises(Exception, match="rate limit"):
            with_retry(fn, agent="test", max_attempts=2, timeout=None)

    def test_fatal_error_raises_immediately(self):
        attempts = []
        def fn():
            attempts.append(1)
            raise Exception("authentication failed")
        with pytest.raises(Exception, match="authentication"):
            with_retry(fn, agent="test", max_attempts=5, timeout=None)
        assert len(attempts) == 1  # no retry

    def test_parse_error_retries(self):
        attempts = []
        def fn():
            attempts.append(1)
            if len(attempts) < 2:
                raise ValueError("could not parse response")
            return "parsed"
        result = with_retry(fn, agent="test", parse_max_attempts=2, timeout=None)
        assert result == "parsed"
        assert len(attempts) == 2

    def test_parse_error_raises_after_max_parse_attempts(self):
        def fn():
            raise ValueError("json parse error")
        with pytest.raises(ValueError):
            with_retry(fn, agent="test", parse_max_attempts=1, timeout=None)

    def test_timeout_raises_timeout_error(self):
        import time
        def slow_fn():
            time.sleep(5)
            return "done"
        with pytest.raises(TimeoutError):
            with_retry(slow_fn, agent="test", timeout=0.1)

    def test_args_and_kwargs_forwarded(self):
        def fn(a, b, key=None):
            return f"{a}-{b}-{key}"
        result = with_retry(fn, "x", "y", agent="test", timeout=None, key="z")
        assert result == "x-y-z"

    def test_success_after_transient_logs_recovery(self, caplog):
        import logging
        attempts = []
        def fn():
            attempts.append(1)
            if len(attempts) == 1:
                raise Exception("timeout")
            return "ok"
        with caplog.at_level(logging.INFO, logger="backend.retry"):
            with_retry(fn, agent="myagent", max_attempts=3, timeout=None)
        assert any("retry.success" in r.message for r in caplog.records)


# ── TokenBucketRateLimiter ──────────────────────────────────────────────────

class TestTokenBucketRateLimiter:
    def test_acquire_does_not_wait_when_tokens_available(self):
        limiter = TokenBucketRateLimiter(capacity=5, refill_per_second=1)
        waited = limiter.acquire()
        assert waited == 0.0

    def test_acquire_consumes_one_token_per_call(self):
        limiter = TokenBucketRateLimiter(capacity=2, refill_per_second=100)
        limiter.acquire()
        limiter.acquire()
        assert limiter._tokens < 1.0

    def test_acquire_waits_when_bucket_empty(self):
        limiter = TokenBucketRateLimiter(capacity=1, refill_per_second=20)
        limiter.acquire()  # drains the single token
        start = time.time()
        waited = limiter.acquire()
        elapsed = time.time() - start
        assert waited > 0.0
        assert elapsed >= waited * 0.5  # sanity: actually slept roughly that long

    def test_refill_replenishes_over_time(self):
        limiter = TokenBucketRateLimiter(capacity=1, refill_per_second=1000)
        limiter.acquire()
        time.sleep(0.01)
        # after 0.01s at 1000/s refill, ~10 tokens worth accumulated (capped at capacity=1)
        waited = limiter.acquire()
        assert waited == 0.0

    def test_capacity_caps_accumulated_tokens(self):
        limiter = TokenBucketRateLimiter(capacity=2, refill_per_second=1000)
        time.sleep(0.01)
        limiter._refill_locked()
        assert limiter._tokens <= 2.0


# ── CircuitBreaker ───────────────────────────────────────────────────────────

class TestCircuitBreaker:
    def test_starts_closed(self):
        breaker = CircuitBreaker()
        assert breaker.is_open is False
        breaker.before_call()  # should not raise

    def test_opens_after_threshold_consecutive_failures(self):
        breaker = CircuitBreaker(failure_threshold=3, reset_seconds=60)
        breaker.record_failure()
        breaker.record_failure()
        assert breaker.is_open is False
        breaker.record_failure()
        assert breaker.is_open is True

    def test_before_call_raises_when_open(self):
        breaker = CircuitBreaker(failure_threshold=1, reset_seconds=60)
        breaker.record_failure()
        with pytest.raises(CircuitOpenError):
            breaker.before_call()

    def test_success_resets_failure_count(self):
        breaker = CircuitBreaker(failure_threshold=3, reset_seconds=60)
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_success()
        assert breaker.status()["consecutive_failures"] == 0
        breaker.record_failure()
        breaker.record_failure()
        assert breaker.is_open is False  # only 2 since reset, threshold is 3

    def test_half_opens_after_reset_window(self):
        breaker = CircuitBreaker(failure_threshold=1, reset_seconds=0.05)
        breaker.record_failure()
        assert breaker.is_open is True
        time.sleep(0.06)
        assert breaker.is_open is False  # half-open — before_call should allow through
        breaker.before_call()  # should not raise

    def test_status_reports_state_and_failure_count(self):
        breaker = CircuitBreaker(failure_threshold=5, reset_seconds=60)
        breaker.record_failure()
        breaker.record_failure()
        status = breaker.status()
        assert status["state"] == "closed"
        assert status["consecutive_failures"] == 2


# ── with_retry integration with rate_limiter / circuit_breaker ─────────────

class TestWithRetryProtections:
    def test_defaults_disable_both_protections(self):
        """Regression guard: with_retry must NOT default to shared global
        state, since that caused unrelated tests to interfere with each
        other (rate-limiter token exhaustion, circuit-breaker trips leaking
        across tests) and blew up this file's runtime from <1s to 15+ minutes."""
        import inspect
        sig = inspect.signature(with_retry)
        assert sig.parameters["rate_limiter"].default is None
        assert sig.parameters["circuit_breaker"].default is None

    def test_with_retry_respects_open_circuit_breaker(self):
        breaker = CircuitBreaker(failure_threshold=1, reset_seconds=60)
        breaker.record_failure()

        def fn():
            return "should not be called"

        with pytest.raises(CircuitOpenError):
            with_retry(fn, agent="test", timeout=None, circuit_breaker=breaker)

    def test_with_retry_records_success_on_breaker(self):
        breaker = CircuitBreaker(failure_threshold=3, reset_seconds=60)

        def fn():
            return "ok"

        with_retry(fn, agent="test", timeout=None, circuit_breaker=breaker)

        assert breaker.status()["consecutive_failures"] == 0

    def test_with_retry_records_failure_on_breaker_after_exhausting_retries(self):
        breaker = CircuitBreaker(failure_threshold=5, reset_seconds=60)

        def fn():
            raise Exception("rate limit exceeded")

        with pytest.raises(Exception):
            with_retry(fn, agent="test", max_attempts=1, timeout=None, circuit_breaker=breaker)

        assert breaker.status()["consecutive_failures"] == 1

    def test_with_retry_uses_rate_limiter_before_each_attempt(self):
        limiter = TokenBucketRateLimiter(capacity=10, refill_per_second=1000)
        calls = []

        def fn():
            calls.append(1)
            return "ok"

        with_retry(fn, agent="test", timeout=None, rate_limiter=limiter)

        assert calls == [1]
        assert limiter._tokens < 10.0
