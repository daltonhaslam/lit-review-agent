import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class APIConfig:
    ncbi_api_key: str = ""
    openalex_email: str = ""
    crossref_mailto: str = ""
    unpaywall_email: str = ""
    semantic_scholar_api_key: str = ""
    report_author: str = ""


def _first_env(names: tuple[str, ...], default: str) -> str:
    """First env var that is *set* (even if empty), else default — same precedence as
    ``os.environ.get(name, default)`` for the single-name fields."""
    for name in names:
        if name in os.environ:
            return os.environ[name]
    return default


def load_api_config(keys_file: Optional[Path] = None) -> APIConfig:
    """Load API config from yaml file, with env var overrides."""
    data: dict = {}
    if keys_file is None:
        keys_file = Path(__file__).parent.parent.parent / "config" / "api_keys.yaml"

    if keys_file.exists():
        with keys_file.open() as f:
            data = yaml.safe_load(f) or {}

    return APIConfig(
        ncbi_api_key=os.environ.get("NCBI_API_KEY", data.get("ncbi_api_key", "")),
        openalex_email=os.environ.get("OPENALEX_EMAIL", data.get("openalex_email", "")),
        crossref_mailto=os.environ.get("CROSSREF_MAILTO", data.get("crossref_mailto", "")),
        unpaywall_email=os.environ.get("UNPAYWALL_EMAIL", data.get("unpaywall_email", "")),
        semantic_scholar_api_key=_first_env(
            ("SEMANTIC_SCHOLAR_API_KEY", "S2_API_KEY"),  # S2_API_KEY: legacy name, still honored
            default=data.get("semantic_scholar_api_key") or "",
        ),
        report_author=os.environ.get("REPORT_AUTHOR", data.get("report_author", "")),
    )
