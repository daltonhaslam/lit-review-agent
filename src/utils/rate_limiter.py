from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable

import httpx

# Cap on a server-requested Retry-After: retrieval runs interactively, so one saturated
# source must not hold the whole run for minutes (3 retries × cap = worst case).
MAX_RETRY_AFTER_SECONDS = 30.0


class RateLimiter:
    """Token-bucket rate limiter. One instance per API host."""

    def __init__(self, rate: float, burst: int = 1):
        self._rate = rate       # tokens/second
        self._burst = burst
        self._tokens = float(burst)
        self._last_refill = time.monotonic()
        # Lazy — asyncio.Lock() requires a running event loop (Python 3.9);
        # create on first acquire() call, which is always inside async context.
        self._lock: asyncio.Lock | None = None

    async def acquire(self) -> None:
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
            self._last_refill = now

            if self._tokens < 1:
                wait = (1 - self._tokens) / self._rate
                await asyncio.sleep(wait)
                self._tokens = 0
            else:
                self._tokens -= 1

    @staticmethod
    async def retry_with_backoff(
        func: Callable[[], Any],
        max_retries: int = 3,
        base_delay: float = 1.0,
    ) -> Any:
        for attempt in range(max_retries + 1):
            try:
                return await func()
            except Exception as exc:
                if not _is_transient(exc) or attempt >= max_retries:
                    raise
                delay = _retry_after_seconds(exc)
                if delay is None:
                    delay = base_delay * (2 ** attempt)
                await asyncio.sleep(delay)


def _is_transient(exc: Exception) -> bool:
    """429 or 5xx — worth retrying. Transport errors (timeouts, refused connections) are
    not retried: the PDF downloader runs this over hundreds of hosts with a 60 s timeout."""
    if not isinstance(exc, httpx.HTTPStatusError):
        return False
    code = exc.response.status_code
    return code == 429 or 500 <= code < 600


def _retry_after_seconds(exc: Exception) -> float | None:
    """Delay requested by a Retry-After header (delay-seconds or HTTP-date), clamped to
    [0, MAX_RETRY_AFTER_SECONDS]; None if absent or unparseable."""
    if not isinstance(exc, httpx.HTTPStatusError):
        return None
    raw = exc.response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        try:
            seconds = (parsedate_to_datetime(raw) - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError):
            return None
    return max(0.0, min(seconds, MAX_RETRY_AFTER_SECONDS))


# Shared limiters — one per host, imported by each client
LIMITERS: dict[str, RateLimiter] = {
    "eutils.ncbi.nlm.nih.gov": RateLimiter(rate=9.0),
    "api.openalex.org": RateLimiter(rate=8.0),
    "api.crossref.org": RateLimiter(rate=9.0),
    "api.semanticscholar.org": RateLimiter(rate=0.9),
    "api.unpaywall.org": RateLimiter(rate=5.0),
    "www.ebi.ac.uk": RateLimiter(rate=9.0),
}
