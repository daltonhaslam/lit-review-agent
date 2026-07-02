"""Unit tests for rate limiter (Phase 0)."""
import asyncio
import time
import pytest

from src.utils.rate_limiter import RateLimiter, LIMITERS


# --- token bucket behavior ---

@pytest.mark.asyncio
async def test_acquire_does_not_block_when_tokens_available():
    limiter = RateLimiter(rate=100.0, burst=5)
    start = time.monotonic()
    for _ in range(5):
        await limiter.acquire()
    elapsed = time.monotonic() - start
    assert elapsed < 0.5  # 5 tokens available; should be near-instant


@pytest.mark.asyncio
async def test_acquire_blocks_when_tokens_exhausted():
    limiter = RateLimiter(rate=10.0, burst=1)
    await limiter.acquire()  # consume the one token
    start = time.monotonic()
    await limiter.acquire()  # must wait ~0.1s for next token
    elapsed = time.monotonic() - start
    assert elapsed >= 0.05  # some wait occurred


# --- retry_with_backoff ---

@pytest.mark.asyncio
async def test_retry_succeeds_on_first_try():
    calls = []

    async def succeeds():
        calls.append(1)
        return "ok"

    result = await RateLimiter.retry_with_backoff(succeeds, max_retries=3, base_delay=0.01)
    assert result == "ok"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_retry_retries_on_rate_limit_error():
    attempts = []

    async def fails_then_succeeds():
        attempts.append(1)
        if len(attempts) < 3:
            raise Exception("429 rate limit exceeded")
        return "ok"

    result = await RateLimiter.retry_with_backoff(
        fails_then_succeeds, max_retries=3, base_delay=0.01
    )
    assert result == "ok"
    assert len(attempts) == 3


@pytest.mark.asyncio
async def test_retry_raises_after_max_retries():
    async def always_fails():
        raise Exception("429 rate limit")

    with pytest.raises(Exception, match="429"):
        await RateLimiter.retry_with_backoff(always_fails, max_retries=2, base_delay=0.01)


@pytest.mark.asyncio
async def test_retry_reraises_non_rate_limit_error_immediately():
    attempts = []

    async def non_rate_error():
        attempts.append(1)
        raise ValueError("bad input")

    with pytest.raises(ValueError):
        await RateLimiter.retry_with_backoff(non_rate_error, max_retries=3, base_delay=0.01)

    assert len(attempts) == 1  # should not retry on non-rate-limit errors


# --- shared LIMITERS dict ---

def test_limiters_has_all_expected_hosts():
    expected = {
        "eutils.ncbi.nlm.nih.gov",
        "api.openalex.org",
        "api.crossref.org",
        "api.semanticscholar.org",
        "api.unpaywall.org",
        "www.ebi.ac.uk",
    }
    assert expected.issubset(set(LIMITERS.keys()))


def test_all_limiters_are_rate_limiter_instances():
    for host, limiter in LIMITERS.items():
        assert isinstance(limiter, RateLimiter), f"{host} is not a RateLimiter"
