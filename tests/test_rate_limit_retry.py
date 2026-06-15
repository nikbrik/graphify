"""Tests for rate-limit retry policy (HTTP 429, Retry-After, backoff)."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from graphify import rate_limit


class _FakeResponse:
    def __init__(self, headers: dict | None = None, status_code: int = 429):
        self.headers = headers or {}
        self.status_code = status_code


class FakeRateLimitError(Exception):
    status_code = 429

    def __init__(self, message: str = "rate limited", headers: dict | None = None):
        super().__init__(message)
        self.response = _FakeResponse(headers or {"retry-after": "5"})


def test_parse_retry_after_seconds_integer():
    assert rate_limit.parse_retry_after_seconds({"retry-after": "120"}) == 120.0


def test_parse_retry_after_seconds_float():
    assert rate_limit.parse_retry_after_seconds({"Retry-After": "3.5"}) == 3.5


def test_parse_retry_after_seconds_missing():
    assert rate_limit.parse_retry_after_seconds({}) is None


def test_parse_retry_after_seconds_case_insensitive():
    assert rate_limit.parse_retry_after_seconds({"Retry-After": "45"}) == 45.0
    assert rate_limit.parse_retry_after_seconds({"RETRY-AFTER": "30"}) == 30.0


def test_parse_retry_after_ignores_non_retry_after_headers():
    # x-ratelimit-reset-requests is not a Retry-After seconds value.
    assert rate_limit.parse_retry_after_seconds({"x-ratelimit-reset-requests": "6m0s"}) is None


def test_parse_retry_after_from_body():
    exc = FakeRateLimitError()
    exc.body = {"error": {"retry_after": 90}}
    assert rate_limit.parse_retry_after_seconds({}, exc) == 90.0


def test_is_retryable_llm_error_429():
    assert rate_limit.is_retryable_llm_error(FakeRateLimitError())


def test_is_retryable_llm_error_503():
    exc = Exception("503 service unavailable")
    exc.status_code = 503
    assert rate_limit.is_retryable_llm_error(exc)


def test_is_retryable_llm_error_401_not_retryable():
    exc = Exception("401 unauthorized")
    exc.status_code = 401
    assert not rate_limit.is_retryable_llm_error(exc)


def test_is_retryable_skips_context_overflow():
    def _is_ctx(exc):
        return "context length" in str(exc).lower()

    exc = RuntimeError("context length exceeded")
    assert not rate_limit.is_retryable_llm_error(exc, is_context_overflow=_is_ctx)


def test_compute_backoff_delay_prefers_retry_after():
    cfg = rate_limit.RateLimitConfig(max_wait_per_attempt=600.0)
    delay = rate_limit.compute_backoff_delay(0, retry_after=300.0, config=cfg)
    assert delay <= 600.0
    assert delay >= 300.0 * 0.8


def test_compute_backoff_delay_exponential_without_retry_after():
    cfg = rate_limit.RateLimitConfig(
        max_wait_per_attempt=600.0,
        backoff_base=2.0,
        backoff_multiplier=2.0,
    )
    with patch("graphify.rate_limit.random.uniform", return_value=1.0):
        d0 = rate_limit.compute_backoff_delay(0, retry_after=None, config=cfg)
        d1 = rate_limit.compute_backoff_delay(1, retry_after=None, config=cfg)
    assert d0 == 2.0
    assert d1 == 4.0


def test_call_with_rate_limit_retry_recovers_after_429():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise FakeRateLimitError(headers={"retry-after": "0.01"})
        return "ok"

    cfg = rate_limit.RateLimitConfig(
        max_wait_per_attempt=1.0,
        max_total_wait=10.0,
        max_retries=5,
        backoff_base=0.01,
    )
    with patch("graphify.rate_limit.time.sleep"):
        result = rate_limit.call_with_rate_limit_retry(
            fn, backend="openrouter", config=cfg
        )
    assert result == "ok"
    assert calls["n"] == 3


def test_call_with_rate_limit_retry_disabled():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise FakeRateLimitError()

    cfg = rate_limit.RateLimitConfig(enabled=False)
    with pytest.raises(FakeRateLimitError):
        rate_limit.call_with_rate_limit_retry(fn, config=cfg)
    assert calls["n"] == 1


def test_call_with_rate_limit_retry_exhausted_raises():
    def fn():
        raise FakeRateLimitError(headers={"retry-after": "0.01"})

    cfg = rate_limit.RateLimitConfig(
        max_retries=2,
        max_wait_per_attempt=1.0,
        max_total_wait=10.0,
        backoff_base=0.01,
    )
    with patch("graphify.rate_limit.time.sleep"):
        with pytest.raises(FakeRateLimitError):
            rate_limit.call_with_rate_limit_retry(fn, config=cfg)


def test_is_retryable_llm_error_400_not_retryable():
    exc = Exception("capacity planning error")
    exc.status_code = 400
    assert not rate_limit.is_retryable_llm_error(exc)


def test_is_retryable_llm_error_400_explicit_rate_limit():
    exc = Exception("400 Bad Request: rate limit exceeded for model")
    exc.status_code = 400
    assert rate_limit.is_retryable_llm_error(exc)


def test_exception_status_code_coerces_string():
    exc = Exception("error")
    exc.status_code = "429"
    assert rate_limit._exception_status_code(exc) == 429


def test_exception_status_code_from_boto_response_metadata():
    exc = Exception("error")
    exc.response = {"ResponseMetadata": {"HTTPStatusCode": 503}}
    assert rate_limit._exception_status_code(exc) == 503


def test_is_retryable_llm_error_bedrock_throttling_400():
    exc = Exception("bedrock request failed")
    exc.response = {
        "Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"},
        "ResponseMetadata": {"HTTPStatusCode": 400},
    }
    assert rate_limit.is_retryable_llm_error(exc)


@pytest.mark.parametrize("status", [401, 403, 404])
def test_is_retryable_llm_error_auth_status_wins_over_bedrock_throttle_code(status):
    exc = Exception("bedrock auth/resource error")
    exc.response = {
        "Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"},
        "ResponseMetadata": {"HTTPStatusCode": status},
    }
    assert not rate_limit.is_retryable_llm_error(exc)


def test_sleep_with_heartbeat_waits_for_extended_gate(monkeypatch):
    """Local delay may elapse while the global gate is still active — keep waiting."""
    clock = {"t": 0.0}
    rate_limit._GATE._paused_until = 0.0

    def monotonic():
        return clock["t"]

    def fake_sleep(seconds):
        clock["t"] += seconds
        if clock["t"] == 1.0:
            # Another worker hit rate limit with a longer Retry-After.
            rate_limit._GATE.signal_pause(5.0)

    monkeypatch.setattr(rate_limit.time, "monotonic", monotonic)
    monkeypatch.setattr(rate_limit.time, "sleep", fake_sleep)

    rate_limit._sleep_with_heartbeat(2.0, None, "test")
    assert clock["t"] >= 5.0


def test_is_retryable_does_not_match_status_substrings_in_unrelated_text():
    assert not rate_limit.is_retryable_llm_error(Exception("connection to port 5020 refused"))
    assert rate_limit.is_retryable_llm_error(Exception("Error code: 429 - rate limit"))


def test_sleep_with_heartbeat_returns_elapsed_seconds(monkeypatch):
    clock = {"t": 0.0}
    rate_limit._GATE._paused_until = 0.0

    def monotonic():
        return clock["t"]

    def fake_sleep(seconds):
        clock["t"] += seconds

    monkeypatch.setattr(rate_limit.time, "monotonic", monotonic)
    monkeypatch.setattr(rate_limit.time, "sleep", fake_sleep)

    elapsed = rate_limit._sleep_with_heartbeat(3.0, None, "test")
    assert elapsed == 3.0


def test_call_with_rate_limit_retry_counts_actual_extended_wait(monkeypatch):
    """max_total_wait must reflect real sleep time, not just planned delay."""
    clock = {"t": 0.0}
    rate_limit._GATE._paused_until = 0.0

    def monotonic():
        return clock["t"]

    def fake_sleep(seconds):
        clock["t"] += seconds
        if clock["t"] == 1.0:
            rate_limit._GATE.signal_pause(4.0)

    def fn():
        raise rate_limit.SubprocessRateLimitError("429")

    monkeypatch.setattr(rate_limit.time, "monotonic", monotonic)
    monkeypatch.setattr(rate_limit.time, "sleep", fake_sleep)

    cfg = rate_limit.RateLimitConfig(
        max_wait_per_attempt=10.0,
        max_total_wait=3.0,
        max_retries=5,
        backoff_base=2.0,
        backoff_multiplier=2.0,
    )
    stats = rate_limit.RateLimitCallStats()
    with patch("graphify.rate_limit.random.uniform", return_value=1.0):
        with pytest.raises(rate_limit.SubprocessRateLimitError):
            rate_limit.call_with_rate_limit_retry(fn, config=cfg, stats=stats)

    assert stats.retries == 1
    assert stats.wait_seconds >= 5.0
    assert clock["t"] >= 5.0


def test_is_subprocess_rate_limit_error():
    assert rate_limit.is_subprocess_rate_limit_error("HTTP 429 too many requests", 1)
    assert rate_limit.is_subprocess_rate_limit_error("", 1, stdout="rate limit exceeded")
    assert rate_limit.is_subprocess_rate_limit_error("503 service unavailable", 1)
    assert not rate_limit.is_subprocess_rate_limit_error("auth failed", 1)
    assert not rate_limit.is_subprocess_rate_limit_error("error near line 4290", 1)


def test_gate_pause_increment_deduplicates_overlapping_workers():
    """Global wait must not sum concurrent pauses from parallel workers."""
    rate_limit._GATE._paused_until = 0.0
    first = rate_limit._GATE.signal_pause(10.0)
    second = rate_limit._GATE.signal_pause(10.0)
    assert first == 10.0
    assert second == 0.0
    extended = rate_limit._GATE.signal_pause(15.0)
    assert extended == 5.0


def test_global_wait_uses_deduplicated_gate_increment(monkeypatch):
    rate_limit.reset_global_rate_limit_stats()
    rate_limit._GATE._paused_until = 0.0
    clock = {"t": 0.0}

    def monotonic():
        return clock["t"]

    def fake_sleep(seconds):
        clock["t"] += seconds

    monkeypatch.setattr(rate_limit.time, "monotonic", monotonic)
    monkeypatch.setattr(rate_limit.time, "sleep", fake_sleep)

    cfg = rate_limit.RateLimitConfig(
        max_wait_per_attempt=10.0,
        max_total_wait=30.0,
        max_retries=1,
        backoff_base=10.0,
        backoff_multiplier=2.0,
    )

    def fn():
        raise FakeRateLimitError(headers={"retry-after": "10"})

    with patch("graphify.rate_limit.random.uniform", return_value=1.0):
        with pytest.raises(FakeRateLimitError):
            rate_limit.call_with_rate_limit_retry(fn, config=cfg)

    stats = rate_limit.snapshot_global_rate_limit_stats()
    assert stats["retries"] == 1
    assert stats["wait_seconds"] == 10.0
    assert clock["t"] == 10.0


def test_resolve_rate_limit_config_from_env(monkeypatch):
    monkeypatch.setenv("GRAPHIFY_RATE_LIMIT_MAX_WAIT", "900")
    monkeypatch.setenv("GRAPHIFY_RATE_LIMIT_MAX_RETRIES", "10")
    cfg = rate_limit.resolve_rate_limit_config()
    assert cfg.max_wait_per_attempt == 900.0
    assert cfg.max_retries == 10


def test_global_stats_recorded_on_retry():
    rate_limit.reset_global_rate_limit_stats()
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] == 1:
            raise FakeRateLimitError(headers={"retry-after": "0.01"})
        return 1

    cfg = rate_limit.RateLimitConfig(
        max_wait_per_attempt=1.0,
        max_total_wait=10.0,
        backoff_base=0.01,
    )
    with patch("graphify.rate_limit.time.sleep"):
        rate_limit.call_with_rate_limit_retry(fn, config=cfg)
    stats = rate_limit.snapshot_global_rate_limit_stats()
    assert stats["retries"] == 1
    assert stats["wait_seconds"] > 0


def test_thread_stats_isolated_between_workers():
    """Parallel chunk workers must not count each other's retries."""
    import threading

    rate_limit.reset_global_rate_limit_stats()
    barrier = threading.Barrier(2)
    results: dict[str, int] = {}

    def worker(name: str, fail_times: int):
        rate_limit.reset_thread_rate_limit_stats()
        barrier.wait()
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            if calls["n"] <= fail_times:
                raise FakeRateLimitError(headers={"retry-after": "0.01"})
            return name

        cfg = rate_limit.RateLimitConfig(
            max_wait_per_attempt=1.0,
            max_total_wait=10.0,
            backoff_base=0.01,
        )
        with patch("graphify.rate_limit.time.sleep"):
            rate_limit.call_with_rate_limit_retry(fn, config=cfg)
        results[name] = rate_limit.snapshot_thread_rate_limit_stats().retries

    t1 = threading.Thread(target=worker, args=("a", 1))
    t2 = threading.Thread(target=worker, args=("b", 2))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert results["a"] == 1
    assert results["b"] == 2
    assert rate_limit.snapshot_global_rate_limit_stats()["retries"] == 3
