"""Issue #59: packager must detect Surname YYYY citations in synthesis
narratives that don't correspond to any article in the included set, and
record them in audit['synthesis_citations_unmatched']."""
from __future__ import annotations

from src.skills.packager import _detect_uncited_tokens


def _article(authors, year, **kw):
    return {"authors": list(authors), "year": year, **kw}


def test_detects_surname_year_token_with_no_matching_article():
    articles = [
        _article(["Smith J"], 2020),
        _article(["Jones K"], 2021),
    ]
    narrative = "Smith 2020 found X. Brown 2019 disagreed. Jones 2021 confirmed."
    unmatched = _detect_uncited_tokens(narrative, articles)
    assert unmatched == [{"token": "Brown 2019", "outcome": None}]


def test_handles_combined_authors():
    articles = [_article(["Smith J", "Jones K"], 2020)]
    narrative = "Smith and Jones 2020 reported. Williams & Lee 2018 noted."
    unmatched = _detect_uncited_tokens(narrative, articles)
    # The regex captures "Smith and Jones 2020" as one token; the first-surname
    # split extracts "Smith", which matches the canonical set → suppressed.
    assert any(t["token"].startswith("Williams") for t in unmatched)
    assert not any("Smith" in t["token"] for t in unmatched)


def test_no_false_positives_for_matched_citations():
    articles = [_article(["Smith J"], 2020), _article(["Jones K"], 2021)]
    narrative = "Smith 2020. Jones 2021."
    assert _detect_uncited_tokens(narrative, articles) == []


def test_dedupes_repeated_unmatched_tokens():
    articles = [_article(["Smith J"], 2020)]
    narrative = "Brown 2019 said X. Brown 2019 also said Y."
    unmatched = _detect_uncited_tokens(narrative, articles)
    assert unmatched == [{"token": "Brown 2019", "outcome": None}]


def test_passes_through_unknown_placeholder():
    """Stage 16 sometimes emits 'Unknown 2019' for articles missing authors —
    these are not real unmatched citations, just missing-author markers."""
    articles = []
    narrative = "Unknown 2019 reported foo."
    unmatched = _detect_uncited_tokens(narrative, articles)
    # Caller (cmd_assemble) decides whether to include "Unknown" tokens; this
    # helper returns them as-is.
    assert any(t["token"] == "Unknown 2019" for t in unmatched)


def test_cmd_assemble_populates_synthesis_citations_unmatched(tmp_path):
    """End-to-end: when synthesis cites a surname not in articles, cmd_assemble
    records it in _package_audit.json."""
    import json
    from src.skills.packager import cmd_assemble

    review_dir = tmp_path / "rv"
    review_dir.mkdir()
    (review_dir / "state.json").write_text(json.dumps({
        "review_id": "rv",
        "raw_question": "q",
        "status": "complete",
        "search_first": "2026-05-01",
        "search_last": "2026-05-09",
    }))
    (review_dir / "articles_included.json").write_text(json.dumps([
        {"pmid": "1", "title": "A", "authors": ["Smith J"], "year": 2020}
    ]))
    (review_dir / "synthesis.json").write_text(json.dumps({
        "narrative": {
            "by_outcome": [
                {"outcome": "mortality", "narrative": "Smith 2020 found X. Brown 2019 disagreed."}
            ]
        },
        "executive_summary": "x",
        "limitations": "x",
        "conclusion": "x",
    }))
    (review_dir / "executive_summary.md").write_text(
        "# Executive Summary\n\n"
        "## Background\nStub.\n\n"
        "## Key Findings\nSmith 2020 found things.\n\n"
        "## Clinical and Operational Implications\n- Implication.\n\n"
        "## Conclusion\nConclusion text.\n\n"
        "---\n\n"
        "## Appendix — Supplementary Abstract-Only Evidence (Not Full-Text Appraised)\n"
        "None.\n"
    )
    (review_dir / "quality_report.md").write_text("# Quality\n\nOK.")
    (review_dir / "gap_analysis.md").write_text("# Gaps\n\nNone.")
    (review_dir / "prisma_flow.md").write_text("# PRISMA\n\nFlow.")
    (review_dir / "protocol.md").write_text("# Protocol\n\nDetails.")
    (review_dir / "search_log.md").write_text("# Search Log\n\nDetails.")

    rc = cmd_assemble(review_dir)
    assert rc == 0
    audit = json.loads((review_dir / "_package_audit.json").read_text())
    unmatched = audit["synthesis_citations_unmatched"]
    assert any(u["token"] == "Brown 2019" and u["outcome"] == "mortality" for u in unmatched)
