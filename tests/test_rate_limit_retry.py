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


def test_is_subprocess_rate_limit_error():
    assert rate_limit.is_subprocess_rate_limit_error("HTTP 429 too many requests", 1)
    assert not rate_limit.is_subprocess_rate_limit_error("auth failed", 1)


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
