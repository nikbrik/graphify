"""Rate-limit retry policy for LLM HTTP calls (HTTP 429, 503, transient timeouts)."""
from __future__ import annotations

import json
import os
import random
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable, TypeVar

T = TypeVar("T")

# Module-level metrics aggregated across calls (extract summary reads these).
_global_stats_lock = threading.Lock()
_global_stats: dict[str, float | int] = {
    "retries": 0,
    "wait_seconds": 0.0,
}


def reset_global_rate_limit_stats() -> None:
    """Clear cross-call counters (used at start of extract_corpus_parallel)."""
    with _global_stats_lock:
        _global_stats["retries"] = 0
        _global_stats["wait_seconds"] = 0.0


def snapshot_global_rate_limit_stats() -> dict[str, float | int]:
    with _global_stats_lock:
        return dict(_global_stats)


def _record_global_retry(wait_seconds: float) -> None:
    with _global_stats_lock:
        _global_stats["retries"] = int(_global_stats["retries"]) + 1
        _global_stats["wait_seconds"] = float(_global_stats["wait_seconds"]) + wait_seconds


@dataclass
class RateLimitContext:
    """Optional caller context for stderr logs."""

    chunk_idx: int | None = None
    chunk_total: int | None = None
    operation: str = "extract"
    backend: str = ""


@dataclass
class RateLimitConfig:
    enabled: bool = True
    max_wait_per_attempt: float = 600.0
    max_total_wait: float = 3600.0
    max_retries: int = 25
    backoff_base: float = 2.0
    backoff_multiplier: float = 2.0


@dataclass
class RateLimitCallStats:
    retries: int = 0
    wait_seconds: float = 0.0


# Thread-local context for chunk idx propagation into deep call stacks.
_tls = threading.local()


def set_rate_limit_context(ctx: RateLimitContext | None) -> RateLimitContext | None:
    """Set thread-local rate-limit context; returns previous value."""
    prev = getattr(_tls, "context", None)
    _tls.context = ctx
    return prev


def get_rate_limit_context() -> RateLimitContext | None:
    return getattr(_tls, "context", None)


class _RateLimitGate:
    """Global pause shared across parallel chunk workers."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._paused_until: float = 0.0

    def wait_if_paused(self) -> None:
        while True:
            with self._lock:
                remaining = self._paused_until - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(remaining, 1.0))

    def signal_pause(self, delay_seconds: float) -> None:
        if delay_seconds <= 0:
            return
        with self._lock:
            self._paused_until = max(
                self._paused_until,
                time.monotonic() + delay_seconds,
            )


_GATE = _RateLimitGate()


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        v = float(raw)
        if v > 0:
            return v
    except ValueError:
        print(
            f"[graphify] {name}={raw!r} is not a valid positive number; "
            f"using default {default}.",
            file=sys.stderr,
        )
    return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        v = int(raw)
        if v > 0:
            return v
    except ValueError:
        print(
            f"[graphify] {name}={raw!r} is not a valid positive integer; "
            f"using default {default}.",
            file=sys.stderr,
        )
    return default


def resolve_rate_limit_config() -> RateLimitConfig:
    return RateLimitConfig(
        enabled=_env_bool("GRAPHIFY_RATE_LIMIT_RETRY", True),
        max_wait_per_attempt=_env_float("GRAPHIFY_RATE_LIMIT_MAX_WAIT", 600.0),
        max_total_wait=_env_float("GRAPHIFY_RATE_LIMIT_MAX_TOTAL_WAIT", 3600.0),
        max_retries=_env_int("GRAPHIFY_RATE_LIMIT_MAX_RETRIES", 25),
        backoff_base=_env_float("GRAPHIFY_RATE_LIMIT_BACKOFF_BASE", 2.0),
        backoff_multiplier=_env_float("GRAPHIFY_RATE_LIMIT_BACKOFF_MULTIPLIER", 2.0),
    )


_RETRYABLE_STATUS = frozenset({429, 502, 503, 504})
_RETRYABLE_MARKERS = (
    "rate limit",
    "rate_limit",
    "too many requests",
    "throttl",
    "overloaded",
    "capacity",
)
_NON_RETRYABLE_STATUS = frozenset({401, 403, 404})
_TIMEOUT_MARKERS = ("timeout", "timed out", "read timeout", "connect timeout")


def _exception_status_code(exc: BaseException) -> int | None:
    for attr in ("status_code", "http_status", "status"):
        code = getattr(exc, attr, None)
        if isinstance(code, int):
            return code
    resp = getattr(exc, "response", None)
    if resp is not None:
        code = getattr(resp, "status_code", None)
        if isinstance(code, int):
            return code
    return None


def _exception_headers(exc: BaseException) -> dict:
    resp = getattr(exc, "response", None)
    if resp is None:
        return {}
    headers = getattr(resp, "headers", None)
    if headers is None:
        return {}
    try:
        return dict(headers)
    except Exception:
        return {}


def _body_retry_after(exc: BaseException) -> float | None:
    body = getattr(exc, "body", None)
    if body is None:
        return None
    if isinstance(body, (bytes, bytearray)):
        try:
            body = body.decode("utf-8", errors="replace")
        except Exception:
            return None
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            return None
    if not isinstance(body, dict):
        return None
    err = body.get("error")
    if isinstance(err, dict):
        for key in ("retry_after", "retryAfter"):
            if key in err:
                return _parse_retry_after_value(err[key])
    for key in ("retry_after", "retryAfter"):
        if key in body:
            return _parse_retry_after_value(body[key])
    return None


def _parse_retry_after_value(raw) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        v = float(raw)
        return v if v >= 0 else None
    text = str(raw).strip()
    if not text:
        return None
    try:
        v = float(text)
        return v if v >= 0 else None
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = (dt - datetime.now(timezone.utc)).total_seconds()
        return max(0.0, delta)
    except (TypeError, ValueError, OverflowError):
        return None


def parse_retry_after_seconds(headers: dict, exc: BaseException | None = None) -> float | None:
    """Parse Retry-After from headers or exception body."""
    lowered = {str(k).lower(): v for k, v in headers.items()}
    if "retry-after" in lowered:
        parsed = _parse_retry_after_value(lowered["retry-after"])
        if parsed is not None:
            return parsed
    if exc is not None:
        return _body_retry_after(exc)
    return None


def _looks_like_timeout(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    if "timeout" in name:
        return True
    msg = str(exc).lower()
    return any(m in msg for m in _TIMEOUT_MARKERS)


def _sdk_retryable(exc: BaseException) -> bool:
    """Detect retryable errors via installed SDK exception types."""
    try:
        from openai import APIStatusError, APITimeoutError, RateLimitError

        if isinstance(exc, (RateLimitError, APITimeoutError)):
            return True
        if isinstance(exc, APIStatusError) and exc.status_code in _RETRYABLE_STATUS:
            return True
    except ImportError:
        pass
    try:
        from anthropic import RateLimitError as AnthropicRateLimitError

        if isinstance(exc, AnthropicRateLimitError):
            return True
    except ImportError:
        pass
    return False


def is_retryable_llm_error(exc: BaseException, *, is_context_overflow: Callable[[BaseException], bool] | None = None) -> bool:
    """Return True if the error warrants a rate-limit / transient retry."""
    if is_context_overflow and is_context_overflow(exc):
        return False

    if _sdk_retryable(exc):
        return True

    code = _exception_status_code(exc)
    if code in _NON_RETRYABLE_STATUS:
        return False
    if code in _RETRYABLE_STATUS:
        return True

    # SDK-specific rate limit types
    exc_name = type(exc).__name__
    if exc_name in ("RateLimitError", "ThrottlingException", "TooManyRequestsException", "SubprocessRateLimitError"):
        return True

    msg = str(exc).lower()
    if any(m in msg for m in _RETRYABLE_MARKERS):
        return True
    if "429" in msg or "503" in msg or "502" in msg or "504" in msg:
        return True

    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        err = response.get("Error", {})
        code = str(err.get("Code", ""))
        if code in (
            "ThrottlingException",
            "TooManyRequestsException",
            "ServiceUnavailable",
            "RequestLimitExceeded",
        ):
            return True

    if _looks_like_timeout(exc):
        return True
    return False


def compute_backoff_delay(
    attempt: int,
    *,
    retry_after: float | None,
    config: RateLimitConfig,
    status_code: int | None = None,
) -> float:
    """Compute sleep duration for attempt (0-based), capped per config."""
    cap = config.max_wait_per_attempt
    if status_code in (502, 503, 504):
        cap = min(cap, cap * 0.5)

    if retry_after is not None and retry_after > 0:
        base = min(retry_after, cap)
    else:
        base = min(
            cap,
            config.backoff_base * (config.backoff_multiplier ** attempt),
        )
    jitter = random.uniform(0.8, 1.2)
    return max(0.0, min(cap, base * jitter))


def _format_context(ctx: RateLimitContext | None, backend: str) -> str:
    parts: list[str] = []
    if ctx and ctx.chunk_idx is not None and ctx.chunk_total is not None:
        parts.append(f"chunk {ctx.chunk_idx + 1}/{ctx.chunk_total}")
    b = (ctx.backend if ctx and ctx.backend else backend) or "backend"
    parts.append(f"backend={b}")
    return ", ".join(parts)


def _sleep_with_heartbeat(delay: float, ctx: RateLimitContext | None, backend: str) -> None:
    if delay <= 0:
        return
    deadline = time.monotonic() + delay
    heartbeat_interval = 60.0
    next_heartbeat = time.monotonic() + heartbeat_interval if delay > 120 else deadline + 1

    while True:
        _GATE.wait_if_paused()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        if time.monotonic() >= next_heartbeat and delay > 120:
            ctx_str = _format_context(ctx, backend)
            print(
                f"[graphify] still waiting on rate limit ({ctx_str}); "
                f"{int(remaining)}s remaining...",
                file=sys.stderr,
                flush=True,
            )
            next_heartbeat = time.monotonic() + heartbeat_interval
        time.sleep(min(remaining, 1.0))


def call_with_rate_limit_retry(
    fn: Callable[[], T],
    *,
    backend: str = "",
    context: RateLimitContext | None = None,
    config: RateLimitConfig | None = None,
    is_context_overflow: Callable[[BaseException], bool] | None = None,
    stats: RateLimitCallStats | None = None,
) -> T:
    """Execute ``fn``, retrying on rate-limit and transient errors."""
    cfg = config or resolve_rate_limit_config()
    ctx = context or get_rate_limit_context()
    local_stats = stats if stats is not None else RateLimitCallStats()

    if not cfg.enabled:
        return fn()

    total_waited = 0.0
    attempt = 0

    while True:
        _GATE.wait_if_paused()
        try:
            return fn()
        except Exception as exc:
            if not is_retryable_llm_error(exc, is_context_overflow=is_context_overflow):
                raise
            if attempt >= cfg.max_retries:
                raise
            if total_waited >= cfg.max_total_wait:
                raise

            status = _exception_status_code(exc)
            headers = _exception_headers(exc)
            retry_after = parse_retry_after_seconds(headers, exc)
            delay = compute_backoff_delay(
                attempt,
                retry_after=retry_after,
                config=cfg,
                status_code=status,
            )
            remaining_budget = cfg.max_total_wait - total_waited
            delay = min(delay, remaining_budget, cfg.max_wait_per_attempt)
            if delay <= 0:
                raise

            attempt_num = attempt + 1
            ctx_str = _format_context(ctx, backend)
            retry_hint = f"Retry-After={retry_after:.0f}s" if retry_after else "exponential backoff"
            print(
                f"[graphify] {backend or 'backend'} rate limited ({ctx_str}, "
                f"attempt {attempt_num}/{cfg.max_retries}); waiting {int(delay)}s "
                f"({retry_hint})",
                file=sys.stderr,
                flush=True,
            )

            _GATE.signal_pause(delay)
            _sleep_with_heartbeat(delay, ctx, backend)

            total_waited += delay
            local_stats.retries += 1
            local_stats.wait_seconds += delay
            _record_global_retry(delay)
            attempt += 1


class SubprocessRateLimitError(RuntimeError):
    """Raised when a CLI subprocess stderr indicates rate limiting."""


def is_subprocess_rate_limit_error(stderr: str, returncode: int) -> bool:
    """Best-effort detection of rate limit in CLI subprocess output."""
    if returncode == 0:
        return False
    text = (stderr or "").lower()
    return any(m in text for m in _RETRYABLE_MARKERS) or "429" in text
