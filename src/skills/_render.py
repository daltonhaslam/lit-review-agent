"""Subprocess wrapper around Pandoc. PDF + DOCX. No business logic."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def pandoc_version() -> str | None:
    if not shutil.which("pandoc"):
        return None
    out = subprocess.run(["pandoc", "--version"], capture_output=True, text=True)
    if out.returncode != 0:
        return None
    return out.stdout.splitlines()[0].split()[-1]


def render_pdf(md_path: Path, out_path: Path, header_path: Path | None) -> tuple[int, str]:
    """Render Markdown to PDF via pandoc + xelatex.

    `header_path` is a LaTeX preamble snippet (not a full template); it is passed
    via `--include-in-header` so Pandoc's default template provides all the
    boilerplate (longtable/booktabs setup, \\tightlist, etc.) and `header_path`
    only layers on customization (page numbers, hyperref colors, fonts).
    """
    cmd = [
        "pandoc", str(md_path),
        # Disable raw_tex passthrough so \input{...} or \write18{...} in
        # article fields cannot reach xelatex as executable LaTeX.
        "-f", "markdown-raw_tex",
        "--pdf-engine=xelatex",
        "--toc",
        "-o", str(out_path),
    ]
    if header_path and header_path.exists():
        cmd.extend(["--include-in-header", str(header_path)])
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, proc.stderr


def render_docx(md_path: Path, out_path: Path, reference_path: Path | None) -> tuple[int, str]:
    cmd = ["pandoc", str(md_path), "-f", "markdown-raw_tex", "--toc", "-o", str(out_path)]
    if reference_path and reference_path.exists():
        cmd.extend(["--reference-doc", str(reference_path)])
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, proc.stderr
