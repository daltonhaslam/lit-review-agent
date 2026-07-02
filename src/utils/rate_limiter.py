from __future__ import annotations

import asyncio
import time
from typing import Any, Callable


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
                msg = str(exc)
                is_rate = "429" in msg or "rate" in msg.lower()
                is_server = "503" in msg or "502" in msg or "500" in msg
                if (is_rate or is_server) and attempt < max_retries:
                    delay = base_delay * (2 ** attempt)
                    await asyncio.sleep(delay)
                else:
                    raise


# Shared limiters — one per host, imported by each client
LIMITERS: dict[str, RateLimiter] = {
    "eutils.ncbi.nlm.nih.gov": RateLimiter(rate=9.0),
    "api.openalex.org": RateLimiter(rate=8.0),
    "api.crossref.org": RateLimiter(rate=9.0),
    "api.semanticscholar.org": RateLimiter(rate=0.9),
    "api.unpaywall.org": RateLimiter(rate=5.0),
    "www.ebi.ac.uk": RateLimiter(rate=9.0),
}
