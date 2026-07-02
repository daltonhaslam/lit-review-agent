"""Shared utilities for the prepare/save CLI pattern used by all subscription-mode skills.

Every skill that involves LLM work follows the same two-phase pattern:
  python skill.py --prepare ...   → prints JSON to stdout for Claude to read
  python skill.py --save ...      → accepts Claude's JSON output, validates, writes to disk

This module provides the shared output formatting and input parsing helpers.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Iterator


def prepare_output(data: dict[str, Any]) -> str:
    """Return a compact JSON string ready for Claude to consume.

    Call print(prepare_output(data)) in --prepare mode.
    """
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def error_output(message: str) -> str:
    """Return a standard error JSON string."""
    return json.dumps({"error": message}, ensure_ascii=False)


def parse_save_input(json_str: str) -> dict[str, Any]:
    """Parse JSON string passed via --input-json (or similar flag).

    Raises ValueError with a user-friendly message on parse failure.
    """
    try:
        result = json.loads(json_str)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc
    if not isinstance(result, dict):
        raise ValueError(f"Expected a JSON object, got {type(result).__name__}")
    return result


def die(message: str, code: int = 1) -> None:
    """Print error JSON to stdout and exit."""
    print(error_output(message))
    sys.exit(code)


def validate_slice_spec(slice_of: int | None, slice_index: int | None) -> None:
    """Validate the --slice-of/--slice-index pair used by parallel sub-agent skills."""
    if (slice_of is None) != (slice_index is None):
        die("--slice-of and --slice-index must be provided together.")
    if slice_of is not None:
        if slice_of <= 0 or slice_index < 0 or slice_index >= slice_of:
            die(f"Invalid slice spec: slice_of={slice_of}, slice_index={slice_index}.")


def iter_pdfs(directory: Path) -> Iterator[Path]:
    """Yield top-level PDF files in a directory, matching the extension case-insensitively.

    pathlib's `glob("*.pdf")` is case-sensitive on case-sensitive filesystems (APFS, ext4), so a
    file named `X.PDF` (Windows origin, some publisher Content-Disposition) is silently skipped —
    stranding the article at abstract-only assessment despite the PDF being present. Files only
    (subdirectories like `pdfs/_rejected/` are skipped).
    """
    if not directory.exists():
        return
    for p in sorted(directory.iterdir()):
        if p.is_file() and p.suffix.lower() == ".pdf":
            yield p


def slice_bounds(total: int, slice_of: int, slice_index: int) -> tuple[int, int]:
    """Balanced contiguous partition: returns [start, end) for slice_index of slice_of."""
    base = total // slice_of
    extra = total % slice_of
    start = slice_index * base + min(slice_index, extra)
    end = start + base + (1 if slice_index < extra else 0)
    return start, end
