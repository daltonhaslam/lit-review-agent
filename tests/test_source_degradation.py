"""Source degradation must surface as a retriever warning, never as a silent zero.

Background: the unauthenticated Semantic Scholar pool returns HTTP 429 persistently.
Before this fix, `SemanticScholarClient.search()` swallowed the exception and returned
`[]`, so `multi_source_retriever` recorded 0 hits with only a vague "may be rate-limited"
warning. Hit on the 2026-09-03 and 2026-09-09 reviews.

All tests here are offline — HTTP is mocked at the client `_get` boundary and
backoff sleeps are patched out.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.agent import state as state_manager
from src.integrations.config import load_api_config
from src.integrations.errors import SourceRetrievalError
from src.integrations.europepmc_client import EuropePMCClient
from src.integrations.openalex_client import OpenAlexClient
from src.integrations.semantic_scholar_client import SEARCH_MAX_RETRIES, SemanticScholarClient
from src.models.article import Article
from src.utils.rate_limiter import RateLimiter

from tests.test_skills_phase2 import _make_article, _make_review


def _http_error(status: int, headers: dict | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://api.semanticscholar.org/graph/v1/paper/search")
    response = httpx.Response(status, headers=headers or {}, request=request)
    return httpx.HTTPStatusError(
        f"Client error '{status}' for url", request=request, response=response
    )


_S2_PAGE = {
    "total": 1,
    "data": [{"paperId": "s2x", "title": "A hit", "year": 2021, "authors": []}],
}


# ---------------------------------------------------------------------------
# RateLimiter.retry_with_backoff
# ---------------------------------------------------------------------------

class TestRetryWithBackoff:
    async def test_retries_429_detected_by_status_code(self):
        calls = 0

        async def flaky():
            nonlocal calls
            calls += 1
            if calls < 3:
                # Message deliberately lacks the digits "429" — detection must use the status code.
                err = _http_error(429)
                err.args = ("too many requests",)
                raise err
            return "ok"

        with patch("src.utils.rate_limiter.asyncio.sleep", new=AsyncMock()):
            result = await RateLimiter.retry_with_backoff(flaky, max_retries=3, base_delay=1.0)
        assert result == "ok"
        assert calls == 3

    async def test_honors_retry_after_header(self):
        calls = 0

        async def flaky():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise _http_error(429, headers={"Retry-After": "7"})
            return "ok"

        sleep = AsyncMock()
        with patch("src.utils.rate_limiter.asyncio.sleep", new=sleep):
            await RateLimiter.retry_with_backoff(flaky, max_retries=2, base_delay=1.0)
        assert sleep.await_args_list[0].args[0] == 7

    async def test_honors_http_date_retry_after_and_caps_it(self):
        from src.utils.rate_limiter import MAX_RETRY_AFTER_SECONDS

        calls = 0

        async def flaky():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise _http_error(429, headers={"Retry-After": "Wed, 21 Oct 2099 07:28:00 GMT"})
            return "ok"

        sleep = AsyncMock()
        with patch("src.utils.rate_limiter.asyncio.sleep", new=sleep):
            await RateLimiter.retry_with_backoff(flaky, max_retries=2, base_delay=1.0)
        assert sleep.await_args_list[0].args[0] == MAX_RETRY_AFTER_SECONDS

    async def test_negative_retry_after_clamps_to_zero(self):
        calls = 0

        async def flaky():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise _http_error(429, headers={"Retry-After": "-5"})
            return "ok"

        sleep = AsyncMock()
        with patch("src.utils.rate_limiter.asyncio.sleep", new=sleep):
            await RateLimiter.retry_with_backoff(flaky, max_retries=2, base_delay=1.0)
        assert sleep.await_args_list[0].args[0] == 0.0

    async def test_transport_errors_are_not_retried(self):
        calls = 0

        async def dead_host():
            nonlocal calls
            calls += 1
            raise httpx.ConnectError("All connection attempts failed")

        with patch("src.utils.rate_limiter.asyncio.sleep", new=AsyncMock()):
            with pytest.raises(httpx.ConnectError):
                await RateLimiter.retry_with_backoff(dead_host, max_retries=3, base_delay=1.0)
        assert calls == 1

    async def test_raises_after_retries_exhausted(self):
        async def always_429():
            raise _http_error(429)

        with patch("src.utils.rate_limiter.asyncio.sleep", new=AsyncMock()):
            with pytest.raises(httpx.HTTPStatusError):
                await RateLimiter.retry_with_backoff(always_429, max_retries=2, base_delay=1.0)


# ---------------------------------------------------------------------------
# SemanticScholarClient.search
# ---------------------------------------------------------------------------

class TestS2SearchSurfaces429:
    async def test_persistent_429_raises_not_silent_zero(self):
        client = SemanticScholarClient()
        with (
            patch.object(client, "_get", new=AsyncMock(side_effect=_http_error(429))),
            patch("src.utils.rate_limiter.asyncio.sleep", new=AsyncMock()),
        ):
            with pytest.raises(SourceRetrievalError) as exc_info:
                await client.search("hospitalist dashboard", max_results=50)
        err = exc_info.value
        assert err.source == "semantic_scholar"
        assert err.status_code == 429
        assert "429" in str(err)
        assert err.partial == []

    async def test_unauthenticated_429_message_points_to_api_key(self):
        client = SemanticScholarClient(api_key="")
        with (
            patch.object(client, "_get", new=AsyncMock(side_effect=_http_error(429))),
            patch("src.utils.rate_limiter.asyncio.sleep", new=AsyncMock()),
        ):
            with pytest.raises(SourceRetrievalError) as exc_info:
                await client.search("q", max_results=10)
        msg = str(exc_info.value)
        assert "unauthenticated" in msg
        assert "semantic_scholar_api_key" in msg

    async def test_authenticated_429_message_does_not_blame_missing_key(self):
        client = SemanticScholarClient(api_key="real-key")
        with (
            patch.object(client, "_get", new=AsyncMock(side_effect=_http_error(429))),
            patch("src.utils.rate_limiter.asyncio.sleep", new=AsyncMock()),
        ):
            with pytest.raises(SourceRetrievalError) as exc_info:
                await client.search("q", max_results=10)
        assert "unauthenticated" not in str(exc_info.value)

    async def test_late_429_keeps_partial_results_on_the_error(self):
        client = SemanticScholarClient()
        page1 = {"total": 300, "data": _S2_PAGE["data"]}
        persistent_429 = [_http_error(429)] * (SEARCH_MAX_RETRIES + 1)
        with (
            patch.object(client, "_get", new=AsyncMock(side_effect=[page1, *persistent_429])),
            patch("src.utils.rate_limiter.asyncio.sleep", new=AsyncMock()),
            patch("src.integrations.semantic_scholar_client.asyncio.sleep", new=AsyncMock()),
        ):
            with pytest.raises(SourceRetrievalError) as exc_info:
                await client.search("q", max_results=200)
        assert exc_info.value.status_code == 429
        assert [a.title for a in exc_info.value.partial] == ["A hit"]

    async def test_genuine_zero_still_returns_empty_list(self):
        client = SemanticScholarClient()
        with patch.object(client, "_get", new=AsyncMock(return_value={"total": 0, "data": []})):
            assert await client.search("nothing matches", max_results=10) == []

    async def test_search_paper_swallows_degradation(self):
        """Enrichment lookups stay best-effort: a 429 yields None, not an exception."""
        client = SemanticScholarClient()
        with (
            patch.object(client, "_get", new=AsyncMock(side_effect=_http_error(429))),
            patch("src.utils.rate_limiter.asyncio.sleep", new=AsyncMock()),
        ):
            assert await client.search_paper("Any title") is None


# ---------------------------------------------------------------------------
# Same silent-zero pattern in the other page-loop clients
# ---------------------------------------------------------------------------

class TestSiblingClientsSurfaceFailure:
    async def test_openalex_persistent_failure_raises(self):
        client = OpenAlexClient(email="")
        with (
            patch.object(client, "_get", new=AsyncMock(side_effect=_http_error(503))),
            patch("src.utils.rate_limiter.asyncio.sleep", new=AsyncMock()),
        ):
            with pytest.raises(SourceRetrievalError) as exc_info:
                await client.search("q", max_results=10)
        assert exc_info.value.source == "openalex"
        assert exc_info.value.status_code == 503

    async def test_europepmc_persistent_failure_raises(self):
        client = EuropePMCClient()
        with (
            patch.object(client, "_get", new=AsyncMock(side_effect=_http_error(429))),
            patch("src.utils.rate_limiter.asyncio.sleep", new=AsyncMock()),
        ):
            with pytest.raises(SourceRetrievalError) as exc_info:
                await client.search("q", max_results=10)
        assert exc_info.value.source == "europepmc"


# ---------------------------------------------------------------------------
# multi_source_retriever run summary
# ---------------------------------------------------------------------------

def _degraded(partial: list[Article] | None = None) -> SourceRetrievalError:
    return SourceRetrievalError(
        "semantic_scholar",
        "HTTP 429 after retries (unauthenticated pool)",
        status_code=429,
        partial=partial or [],
    )


class TestRetrieverRunSummary:
    async def test_degraded_source_is_listed_not_recorded_as_clean_zero(self, tmp_path):
        from src.skills.multi_source_retriever import retrieve

        _, review_dir = _make_review(tmp_path)
        pubmed = [_make_article(pmid="1", title="PubMed hit")]
        with (
            patch("src.skills.multi_source_retriever._search_pubmed", new=AsyncMock(return_value=pubmed)),
            patch("src.skills.multi_source_retriever._search_openalex", new=AsyncMock(return_value=[])),
            patch("src.skills.multi_source_retriever._search_semantic_scholar",
                  new=AsyncMock(side_effect=_degraded())),
        ):
            result = await retrieve(review_dir, sources=["pubmed", "openalex", "semantic_scholar"])

        assert result["by_source"] == {"pubmed": 1, "openalex": 0, "semantic_scholar": 0}
        assert result["degraded_sources"] == ["semantic_scholar"]
        assert result["source_status"] == {
            "pubmed": "ok",
            "openalex": "empty",
            "semantic_scholar": "degraded",
        }
        assert "429" in result["retrieval_errors"]["semantic_scholar"]
        # A genuine zero is still just a warning, not a degradation
        assert "WARNING" in result["retrieval_errors"]["openalex"]

    async def test_degraded_source_marked_in_search_log_and_query_notes(self, tmp_path):
        from src.skills.multi_source_retriever import retrieve

        _, review_dir = _make_review(tmp_path)
        with (
            patch("src.skills.multi_source_retriever._search_pubmed", new=AsyncMock(return_value=[])),
            patch("src.skills.multi_source_retriever._search_openalex", new=AsyncMock(return_value=[])),
            patch("src.skills.multi_source_retriever._search_semantic_scholar",
                  new=AsyncMock(side_effect=_degraded())),
        ):
            await retrieve(review_dir, sources=["pubmed", "openalex", "semantic_scholar"])

        log = (review_dir / "search_log.md").read_text()
        s2_section = log.split("## Semantic Scholar", 1)[1]
        assert "DEGRADED" in s2_section
        assert "429" in s2_section

        saved = state_manager.load(review_dir)
        assert "429" in (saved.queries.semantic_scholar[-1].notes or "")

    async def test_degradation_note_appends_to_existing_query_rationale(self, tmp_path):
        from src.skills.multi_source_retriever import retrieve

        review, review_dir = _make_review(tmp_path)
        review.queries.semantic_scholar[-1].notes = "Stage 4 rationale"
        state_manager.save(review, review_dir)
        with (
            patch("src.skills.multi_source_retriever._search_pubmed", new=AsyncMock(return_value=[])),
            patch("src.skills.multi_source_retriever._search_openalex", new=AsyncMock(return_value=[])),
            patch("src.skills.multi_source_retriever._search_semantic_scholar",
                  new=AsyncMock(side_effect=_degraded())),
        ):
            await retrieve(review_dir, sources=["pubmed", "openalex", "semantic_scholar"])
        notes = state_manager.load(review_dir).queries.semantic_scholar[-1].notes
        assert notes.startswith("Stage 4 rationale")
        assert "429" in notes

    async def test_rerun_does_not_duplicate_degradation_note(self, tmp_path):
        from src.skills.multi_source_retriever import retrieve

        _, review_dir = _make_review(tmp_path)
        patches = (
            patch("src.skills.multi_source_retriever._search_pubmed", new=AsyncMock(return_value=[])),
            patch("src.skills.multi_source_retriever._search_openalex", new=AsyncMock(return_value=[])),
            patch("src.skills.multi_source_retriever._search_semantic_scholar",
                  new=AsyncMock(side_effect=_degraded())),
        )
        for _ in range(2):
            with patches[0], patches[1], patches[2]:
                await retrieve(review_dir, sources=["pubmed", "openalex", "semantic_scholar"])
        notes = state_manager.load(review_dir).queries.semantic_scholar[-1].notes
        assert notes.count("DEGRADED") == 1

    async def test_unwrapped_exception_is_still_reported_as_degraded(self, tmp_path):
        """PubMed raises raw httpx errors; they must get the same degraded treatment."""
        from src.skills.multi_source_retriever import retrieve

        _, review_dir = _make_review(tmp_path)
        with (
            patch("src.skills.multi_source_retriever._search_pubmed",
                  new=AsyncMock(side_effect=_http_error(503))),
            patch("src.skills.multi_source_retriever._search_openalex", new=AsyncMock(return_value=[])),
            patch("src.skills.multi_source_retriever._search_semantic_scholar", new=AsyncMock(return_value=[])),
        ):
            result = await retrieve(review_dir, sources=["pubmed", "openalex", "semantic_scholar"])
        assert result["source_status"]["pubmed"] == "degraded"
        assert "HTTP 503" in result["retrieval_errors"]["pubmed"]

    async def test_partial_results_from_degraded_source_are_kept(self, tmp_path):
        from src.skills.multi_source_retriever import retrieve

        _, review_dir = _make_review(tmp_path)
        partial = [_make_article(s2_id="s2p", title="Partial S2 hit", source_dbs=["semantic_scholar"])]
        with (
            patch("src.skills.multi_source_retriever._search_pubmed", new=AsyncMock(return_value=[])),
            patch("src.skills.multi_source_retriever._search_openalex", new=AsyncMock(return_value=[])),
            patch("src.skills.multi_source_retriever._search_semantic_scholar",
                  new=AsyncMock(side_effect=_degraded(partial))),
        ):
            result = await retrieve(review_dir, sources=["pubmed", "openalex", "semantic_scholar"])

        assert result["by_source"]["semantic_scholar"] == 1
        assert result["total"] == 1
        assert result["degraded_sources"] == ["semantic_scholar"]

    async def test_healthy_run_has_no_degraded_sources(self, tmp_path):
        from src.skills.multi_source_retriever import retrieve

        _, review_dir = _make_review(tmp_path)
        hit = [_make_article(pmid="1", title="hit")]
        with (
            patch("src.skills.multi_source_retriever._search_pubmed", new=AsyncMock(return_value=hit)),
            patch("src.skills.multi_source_retriever._search_openalex", new=AsyncMock(return_value=hit)),
            patch("src.skills.multi_source_retriever._search_semantic_scholar", new=AsyncMock(return_value=hit)),
        ):
            result = await retrieve(review_dir, sources=["pubmed", "openalex", "semantic_scholar"])
        assert result["degraded_sources"] == []
        assert set(result["source_status"].values()) == {"ok"}


# ---------------------------------------------------------------------------
# SEMANTIC_SCHOLAR_API_KEY config
# ---------------------------------------------------------------------------

class TestSemanticScholarKeyConfig:
    def test_yaml_key_is_read(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)
        monkeypatch.delenv("S2_API_KEY", raising=False)
        keys = tmp_path / "api_keys.yaml"
        keys.write_text('semantic_scholar_api_key: "yaml-key"\n')
        assert load_api_config(keys).semantic_scholar_api_key == "yaml-key"

    def test_env_SEMANTIC_SCHOLAR_API_KEY_overrides_yaml(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "env-key")
        monkeypatch.delenv("S2_API_KEY", raising=False)
        keys = tmp_path / "api_keys.yaml"
        keys.write_text('semantic_scholar_api_key: "yaml-key"\n')
        assert load_api_config(keys).semantic_scholar_api_key == "env-key"

    def test_legacy_S2_API_KEY_still_honored(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)
        monkeypatch.setenv("S2_API_KEY", "legacy-key")
        assert load_api_config(tmp_path / "missing.yaml").semantic_scholar_api_key == "legacy-key"

    def test_explicitly_empty_env_var_forces_unauthenticated(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "")
        keys = tmp_path / "api_keys.yaml"
        keys.write_text('semantic_scholar_api_key: "revoked-key"\n')
        assert load_api_config(keys).semantic_scholar_api_key == ""

    def test_blank_everywhere_falls_back_to_unauthenticated(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)
        monkeypatch.delenv("S2_API_KEY", raising=False)
        assert load_api_config(tmp_path / "missing.yaml").semantic_scholar_api_key == ""
