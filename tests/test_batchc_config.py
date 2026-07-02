"""Batch C: `APIConfig` must not expose the removed browser-automation / proxy fields.

The pre-open-source config surface carried several fields for institutional
proxy access (session cookies, SSO credentials, headless-browser automation
credentials). That surface was removed for the public release; this test
guards against it silently coming back, and confirms the loader still works
correctly for the remaining open-access API fields.

All I/O is against a tmp file — no live network or real config.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.integrations.config import APIConfig, load_api_config

_OPEN_ACCESS_FIELDS = {
    "ncbi_api_key",
    "openalex_email",
    "crossref_mailto",
    "unpaywall_email",
    "semantic_scholar_api_key",
    "report_author",
}


def _write_keys(tmp_path: Path, body: str) -> Path:
    keys_file = tmp_path / "api_keys.yaml"
    keys_file.write_text(body)
    return keys_file


class TestConfigSurfaceIsOpenAccessOnly:
    def test_api_config_fields_match_open_access_surface(self):
        assert set(APIConfig.__dataclass_fields__) == _OPEN_ACCESS_FIELDS

    def test_load_api_config_reads_open_access_keys(self, tmp_path):
        keys_file = _write_keys(
            tmp_path,
            "ncbi_api_key: abc123\n"
            "openalex_email: researcher@example.edu\n",
        )
        cfg = load_api_config(keys_file)
        assert cfg.ncbi_api_key == "abc123"
        assert cfg.openalex_email == "researcher@example.edu"
