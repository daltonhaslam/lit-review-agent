"""Semantic Scholar API key: config → x-api-key header, and blank key → degraded source in
the final report.

Complements tests/test_source_degradation.py, which mocks at the client `_get` boundary.
Here HTTP is mocked one layer lower with httpx.MockTransport, so the assertions cover the
real request the client builds (URL, headers) rather than the arguments to a helper.
All offline: the transport never opens a socket, and rate-limit/backoff waits are patched.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.integrations import semantic_scholar_client as s2_module
from src.integrations.config import load_api_config
from src.integrations.semantic_scholar_client import SemanticScholarClient
from src.skills import packager

from tests.test_skills_phase2 import _make_review

_RealAsyncClient = httpx.AsyncClient
_S2_PAGE = {
    "total": 1,
    "data": [{"paperId": "s2x", "title": "A hit", "year": 2021, "authors": []}],
}


def _mock_http(handler):
    """Patch the S2 client's httpx.AsyncClient to route every request through ``handler``."""
    transport = httpx.MockTransport(handler)
    return patch.object(
        s2_module.httpx,
        "AsyncClient",
        new=lambda *a, **kw: _RealAsyncClient(*a, transport=transport, **kw),
    )


def _no_waits():
    return (
        patch.object(s2_module.LIMITER, "acquire", new=AsyncMock()),
        patch("src.utils.rate_limiter.asyncio.sleep", new=AsyncMock()),
        patch("src.integrations.semantic_scholar_client.asyncio.sleep", new=AsyncMock()),
    )


class _Recorder:
    def __init__(self, status: int = 200, body=None):
        self.requests: list[httpx.Request] = []
        self._status = status
        self._body = _S2_PAGE if body is None else body

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self._status != 200:
            return httpx.Response(self._status)
        return httpx.Response(200, json=self._body)


class TestApiKeyHeader:
    async def test_configured_key_is_sent_as_x_api_key_on_search(self):
        rec = _Recorder()
        acquire, sleep1, sleep2 = _no_waits()
        with _mock_http(rec), acquire, sleep1, sleep2:
            await SemanticScholarClient(api_key="test-key-123").search("q", max_results=5)
        assert rec.requests, "no request reached the transport"
        assert all(r.headers.get("x-api-key") == "test-key-123" for r in rec.requests)
        assert rec.requests[0].url.host == "api.semanticscholar.org"

    async def test_configured_key_is_sent_as_x_api_key_on_batch_lookup(self):
        rec = _Recorder(body=_S2_PAGE["data"])
        acquire, sleep1, sleep2 = _no_waits()
        with _mock_http(rec), acquire, sleep1, sleep2:
            await SemanticScholarClient(api_key="test-key-123").batch_lookup(["DOI:10.1/x"])
        assert rec.requests[0].method == "POST"
        assert rec.requests[0].headers.get("x-api-key") == "test-key-123"

    async def test_blank_key_sends_no_x_api_key_header(self):
        rec = _Recorder()
        acquire, sleep1, sleep2 = _no_waits()
        with _mock_http(rec), acquire, sleep1, sleep2:
            await SemanticScholarClient(api_key="").search("q", max_results=5)
        assert "x-api-key" not in rec.requests[0].headers

    async def test_key_in_api_keys_yaml_reaches_the_header(self, tmp_path, monkeypatch):
        """End to end through the retriever's own client construction."""
        from src.skills.multi_source_retriever import _search_semantic_scholar

        monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)
        monkeypatch.delenv("S2_API_KEY", raising=False)
        keys = tmp_path / "api_keys.yaml"
        keys.write_text('semantic_scholar_api_key: "yaml-key-456"\n')
        cfg = load_api_config(keys)

        rec = _Recorder()
        acquire, sleep1, sleep2 = _no_waits()
        with _mock_http(rec), acquire, sleep1, sleep2:
            await _search_semantic_scholar("q", 5, None, cfg)
        assert rec.requests[0].headers.get("x-api-key") == "yaml-key-456"


class TestBlankKeyDegradationReachesReport:
    async def test_persistent_429_without_key_appears_in_packaged_methods(
        self, tmp_path, monkeypatch
    ):
        from src.skills.multi_source_retriever import retrieve

        # Explicitly empty env var forces the unauthenticated pool regardless of local config.
        monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "")
        _, review_dir = _make_review(tmp_path)

        rec = _Recorder(status=429)
        acquire, sleep1, sleep2 = _no_waits()
        with _mock_http(rec), acquire, sleep1, sleep2:
            result = await retrieve(review_dir, sources=["semantic_scholar"])

        assert "x-api-key" not in rec.requests[0].headers
        assert result["degraded_sources"] == ["semantic_scholar"]
        search_log = (review_dir / "search_log.md").read_text()
        assert "DEGRADED: HTTP 429" in search_log
        assert "semanticscholar.org/product/api" in search_log

        # The packaged report's Methods section embeds search_log.md verbatim.
        pkg_dir = tmp_path / "reviews" / "fake-review-2026"
        shutil.copytree(PROJECT_ROOT / "tests" / "fixtures" / "fake_review", pkg_dir)
        (pkg_dir / "search_log.md").write_text(search_log)
        assert packager.cmd_assemble(pkg_dir) == 0
        package_md = (pkg_dir / "package.md").read_text()
        methods = package_md.split("## 2. Methods", 1)[1].split("## 3. Findings", 1)[0]
        assert "DEGRADED: HTTP 429" in methods
        assert "semantic_scholar_api_key" in methods
