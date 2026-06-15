"""Rate-limit retry policy for LLM HTTP calls (HTTP 429, 503, transient timeouts)."""
from __future__ import annotations

import json
import os
import random
import re
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


def reset_thread_rate_limit_stats() -> None:
    """Clear per-thread retry counters (start of each parallel chunk worker)."""
    _tls.stats = RateLimitCallStats()


def snapshot_thread_rate_limit_stats() -> RateLimitCallStats:
    stats = getattr(_tls, "stats", None)
    if stats is None:
        return RateLimitCallStats()
    return RateLimitCallStats(retries=stats.retries, wait_seconds=stats.wait_seconds)


def _record_thread_retry(wait_seconds: float) -> None:
    stats = getattr(_tls, "stats", None)
    if stats is None:
        stats = RateLimitCallStats()
        _tls.stats = stats
    stats.retries += 1
    stats.wait_seconds += wait_seconds


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

    def signal_pause(self, delay_seconds: float) -> float:
        """Extend global pause; return newly added seconds (deduped for metrics)."""
        if delay_seconds <= 0:
            return 0.0
        with self._lock:
            now = time.monotonic()
            prev_effective = max(self._paused_until, now)
            new_until = max(self._paused_until, now + delay_seconds)
            increment = new_until - prev_effective
            self._paused_until = new_until
        return increment

    def remaining_pause_seconds(self) -> float:
        with self._lock:
            return max(0.0, self._paused_until - time.monotonic())


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
_NON_RETRYABLE_STATUS = frozenset({400, 401, 403, 404})
_TIMEOUT_MARKERS = ("timeout", "timed out", "read timeout", "connect timeout")


def _coerce_http_status(code) -> int | None:
    if isinstance(code, int):
        return code
    if isinstance(code, str):
        text = code.strip()
        if text.isdigit():
            return int(text)
        match = re.match(r"(\d{3})", text)
        if match:
            return int(match.group(1))
    return None


def _exception_status_code(exc: BaseException) -> int | None:
    for attr in ("status_code", "http_status", "status"):
        parsed = _coerce_http_status(getattr(exc, attr, None))
        if parsed is not None:
            return parsed
    resp = getattr(exc, "response", None)
    if resp is not None:
        parsed = _coerce_http_status(getattr(resp, "status_code", None))
        if parsed is not None:
            return parsed
        if isinstance(resp, dict):
            meta = resp.get("ResponseMetadata") or {}
            parsed = _coerce_http_status(meta.get("HTTPStatusCode"))
            if parsed is not None:
                return parsed
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
        if isinstance(exc, APIStatusError):
            if exc.status_code in _NON_RETRYABLE_STATUS:
                return False
            return exc.status_code in _RETRYABLE_STATUS
    except ImportError:
        pass
    try:
        from anthropic import RateLimitError as AnthropicRateLimitError

        if isinstance(exc, AnthropicRateLimitError):
            return True
    except ImportError:
        pass
    return False


def _message_indicates_http_status(msg: str, code: int) -> bool:
    return re.search(rf"\b{code}\b", msg) is not None


def _is_explicit_rate_limit_message(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(
        m in msg
        for m in ("rate limit", "rate_limit", "too many requests", "throttl", "overloaded")
    )


def is_retryable_llm_error(exc: BaseException, *, is_context_overflow: Callable[[BaseException], bool] | None = None) -> bool:
    """Return True if the error warrants a rate-limit / transient retry."""
    if is_context_overflow and is_context_overflow(exc):
        return False

    code = _exception_status_code(exc)
    if code in (401, 403, 404):
        return False

    # Some proxies return HTTP 400 with an explicit rate-limit message (not context overflow).
    if code == 400 and _is_explicit_rate_limit_message(exc):
        return True

    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        err = response.get("Error", {})
        err_code = str(err.get("Code", ""))
        if err_code in (
            "ThrottlingException",
            "TooManyRequestsException",
            "ServiceUnavailable",
            "RequestLimitExceeded",
        ):
            return True

    if _sdk_retryable(exc):
        return True

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
    if any(_message_indicates_http_status(msg, code) for code in (429, 502, 503, 504)):
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


def _sleep_with_heartbeat(delay: float, ctx: RateLimitContext | None, backend: str) -> float:
    """Sleep up to ``delay`` seconds while honouring the global gate; return elapsed time."""
    if delay <= 0:
        return 0.0
    start = time.monotonic()
    deadline = start + delay
    heartbeat_interval = 60.0
    next_heartbeat = start + heartbeat_interval if delay > 120 else deadline + 1

    while True:
        now = time.monotonic()
        # Honour both this worker's delay and any longer global gate set by peers.
        remaining = max(deadline - now, _GATE.remaining_pause_seconds())
        if remaining <= 0:
            return now - start
        if now >= next_heartbeat and delay > 120:
            ctx_str = _format_context(ctx, backend)
            print(
                f"[graphify] still waiting on rate limit ({ctx_str}); "
                f"{int(remaining)}s remaining...",
                file=sys.stderr,
                flush=True,
            )
            next_heartbeat = now + heartbeat_interval
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
            retry_hint = (
                f"Retry-After={retry_after:.0f}s"
                if retry_after and retry_after >= 1
                else (f"Retry-After={retry_after:.2f}s" if retry_after else "exponential backoff")
            )
            print(
                f"[graphify] {backend or 'backend'} rate limited ({ctx_str}, "
                f"attempt {attempt_num}/{cfg.max_retries}); waiting {int(delay)}s "
                f"({retry_hint})",
                file=sys.stderr,
                flush=True,
            )

            pause_increment = _GATE.signal_pause(delay)
            actual_wait = _sleep_with_heartbeat(delay, ctx, backend)

            total_waited += actual_wait
            local_stats.retries += 1
            local_stats.wait_seconds += actual_wait
            _record_global_retry(pause_increment)
            _record_thread_retry(actual_wait)
            attempt += 1


class SubprocessRateLimitError(RuntimeError):
    """Raised when a CLI subprocess stderr indicates rate limiting."""


def is_subprocess_rate_limit_error(
    stderr: str,
    returncode: int,
    stdout: str = "",
) -> bool:
    """Best-effort detection of rate limit in CLI subprocess output."""
    if returncode == 0:
        return False
    text = f"{stderr or ''}\n{stdout or ''}".lower()
    if any(m in text for m in _RETRYABLE_MARKERS):
        return True
    return any(_message_indicates_http_status(text, code) for code in _RETRYABLE_STATUS)
