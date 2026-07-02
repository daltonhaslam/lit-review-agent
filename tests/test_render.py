import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _has(tool: str) -> bool:
    return shutil.which(tool) is not None


@pytest.mark.skipif(not _has("pandoc"), reason="pandoc not installed")
def test_render_docx_smoke(tmp_path):
    from src.skills import _render
    md = tmp_path / "in.md"
    md.write_text("# Hello\n\nBody.")
    out = tmp_path / "out.docx"
    rc, stderr = _render.render_docx(md, out, reference_path=None)
    assert rc == 0, stderr
    assert out.exists()
    assert out.stat().st_size > 100
    # DOCX is a zip
    import zipfile
    assert zipfile.is_zipfile(out)


@pytest.mark.skipif(
    not (_has("pandoc") and _has("xelatex")),
    reason="pandoc or xelatex not installed",
)
def test_render_pdf_smoke(tmp_path):
    from src.skills import _render
    md = tmp_path / "in.md"
    md.write_text("# Hello\n\nBody.")
    out = tmp_path / "out.pdf"
    rc, stderr = _render.render_pdf(md, out, header_path=None)
    assert rc == 0, stderr
    assert out.exists()
    assert out.stat().st_size > 5000


def test_render_module_importable_without_pandoc():
    """Module must be importable even on machines without pandoc."""
    from src.skills import _render
    assert hasattr(_render, "render_pdf")
    assert hasattr(_render, "render_docx")
    assert hasattr(_render, "pandoc_version")
