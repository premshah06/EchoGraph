"""Unit tests for the retry handler."""

from __future__ import annotations

import pytest

from backend.retry import _backoff_wait, _classify, with_retry


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
