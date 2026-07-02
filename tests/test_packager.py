import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent


def run_packager(*args, cwd=REPO_ROOT):
    cmd = [sys.executable, "-m", "src.skills.packager", *args]
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def test_scan_returns_complete_reviews(tmp_path):
    reviews_dir = tmp_path / "reviews"
    review_a = reviews_dir / "alpha-2026-05"
    review_b = reviews_dir / "beta-2026-05"
    review_a.mkdir(parents=True)
    review_b.mkdir(parents=True)
    (review_a / "state.json").write_text(json.dumps({
        "review_id": "alpha-2026-05", "raw_question": "q", "status": "complete"
    }))
    (review_b / "state.json").write_text(json.dumps({
        "review_id": "beta-2026-05", "raw_question": "q", "status": "synthesized"
    }))

    result = run_packager("--scan", str(reviews_dir))
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    slugs = [r["review_id"] for r in payload["reviews"]]
    assert "alpha-2026-05" in slugs
    assert "beta-2026-05" not in slugs


def test_scan_handles_missing_directory(tmp_path):
    result = run_packager("--scan", str(tmp_path / "does-not-exist"))
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["reviews"] == []


def _copy_fake_review(tmp_path: Path) -> Path:
    src = REPO_ROOT / "tests" / "fixtures" / "fake_review"
    dst = tmp_path / "reviews" / "fake-review-2026"
    shutil.copytree(src, dst)
    return dst


def test_validate_passes_on_complete_review(tmp_path, monkeypatch):
    review_dir = _copy_fake_review(tmp_path)
    # Force pandoc/xelatex to look "installed" by stubbing shutil.which:
    import src.skills.packager as packager
    monkeypatch.setattr(packager, "_which", lambda name: f"/usr/local/bin/{name}")
    rc = packager.cmd_validate(review_dir)
    assert rc == 0


def test_validate_fails_when_missing_source_file(tmp_path, monkeypatch):
    review_dir = _copy_fake_review(tmp_path)
    (review_dir / "synthesis.json").unlink()
    import src.skills.packager as packager
    monkeypatch.setattr(packager, "_which", lambda name: f"/usr/local/bin/{name}")
    rc = packager.cmd_validate(review_dir)
    assert rc != 0


def test_validate_rejects_quick_review(tmp_path, monkeypatch):
    review_dir = _copy_fake_review(tmp_path)
    state_path = review_dir / "state.json"
    state = json.loads(state_path.read_text())
    state["review_type"] = "narrative"  # /lit-review-quick uses narrative
    state_path.write_text(json.dumps(state))
    # Also remove the quality_report.md to mirror what a quick review folder looks like:
    (review_dir / "quality_report.md").unlink()
    import src.skills.packager as packager
    monkeypatch.setattr(packager, "_which", lambda name: f"/usr/local/bin/{name}")
    rc = packager.cmd_validate(review_dir)
    assert rc != 0


def test_validate_fails_when_pandoc_missing(tmp_path, monkeypatch):
    review_dir = _copy_fake_review(tmp_path)
    import src.skills.packager as packager
    monkeypatch.setattr(packager, "_which", lambda name: None if name == "pandoc" else "/x")
    rc = packager.cmd_validate(review_dir)
    assert rc != 0


import src.skills.packager as packager


def test_format_authors_single():
    assert packager._format_authors(["Marcin James P."]) == "Marcin JP."


def test_format_authors_multiple_uses_et_al():
    formatted = packager._format_authors(["Marcin James P.", "Smith John", "Doe Jane"])
    assert formatted == "Marcin JP, et al."


def test_citation_header_full():
    article = {
        "authors": ["Marcin James P.", "Smith John"], "year": 2023,
        "title": "Telemedicine reduces LOS in rural EDs",
        "journal": "Pediatrics", "volume": "150", "issue": "2", "pages": "e1-e10",
        "doi": "10.1/a",
    }
    header = packager._build_citation_header(article)
    assert "**Marcin JP, et al. (2023).**" in header
    assert "*Telemedicine reduces LOS in rural EDs.*" in header
    assert "Pediatrics" in header
    assert "150(2):e1-e10" in header
    assert "DOI: 10.1/a" in header


def test_citation_header_drops_missing_fields():
    article = {
        "authors": ["Goetz Matthew"], "year": 2024,
        "title": "An editorial on telehealth in 2024", "journal": "BMJ",
    }
    header = packager._build_citation_header(article)
    assert "Goetz M. (2024)." in header
    assert "BMJ" in header
    assert "DOI:" not in header
    assert "(" not in header.split("BMJ")[1] or ":" not in header.split("BMJ")[1]


def test_meta_line_full():
    article = {
        "study_design": "rct", "evidence_level": "II",
        "quality_score": 0.78, "quality_instrument": "RoB 2.0",
        "sample_size": 696,
    }
    line = packager._build_meta_line(article)
    assert "Design: RCT" in line
    assert "Level: II" in line
    assert "Quality: 0.78 (RoB 2.0)" in line
    assert "N = 696" in line


def test_meta_line_drops_missing_fields():
    article = {"study_design": "narrative_review", "evidence_level": "IV"}
    line = packager._build_meta_line(article)
    assert "Design: Narrative review" in line
    assert "Level: IV" in line
    assert "Quality:" not in line
    assert "N =" not in line


def test_meta_line_returns_empty_when_all_fields_missing():
    assert packager._build_meta_line({}) == ""


def test_tldr_uses_tldr_field_when_present():
    article = {"tldr": "Telemedicine reduces transfer.", "abstract": "Long abstract..."}
    assert packager._build_tldr(article) == "Telemedicine reduces transfer."


def test_tldr_falls_back_to_first_sentence_of_abstract():
    article = {"abstract": "Background: rural EDs lack pediatric specialists. Methods: cluster RCT..."}
    assert packager._build_tldr(article) == "Background: rural EDs lack pediatric specialists."


def test_tldr_returns_none_when_both_missing():
    assert packager._build_tldr({}) is None


def test_tldr_returns_none_when_fields_blank():
    assert packager._build_tldr({"tldr": "", "abstract": "   "}) is None


def test_key_findings_under_cap_no_suffix():
    article = {
        "primary_outcomes": ["Transfer", "LOS"],
        "effect_sizes": "Transfer RR 0.93; LOS -2 days.",
    }
    findings = packager._build_key_findings(article)
    assert len(findings) == 3  # 2 outcomes + 1 effect-sizes line
    assert findings[0].startswith("Transfer")
    assert "+ more" not in " ".join(findings)


def test_key_findings_caps_and_appends_suffix():
    article = {
        "primary_outcomes": ["A", "B", "C", "D", "E", "F"],
        "effect_sizes": "Effect sizes detail.",
    }
    findings = packager._build_key_findings(article)
    assert len(findings) == 4
    assert findings[-1] == "+3 more outcomes (see evidence_table.csv)"


def test_key_findings_empty_when_both_missing():
    assert packager._build_key_findings({}) == []


def test_conclusion_under_cap_unchanged():
    text = "Telemedicine reduces transfers."
    out, original, capped = packager._truncate_conclusion(text)
    assert out == text
    assert original == 3
    assert capped is False


def test_conclusion_over_cap_truncated_with_ellipsis():
    text = " ".join(["word"] * 80)
    out, original, capped = packager._truncate_conclusion(text)
    assert original == 80
    assert capped is True
    assert out.endswith("…")
    assert len(out.split()) <= 51  # 50 words + ellipsis token


def test_conclusion_blank_returns_none():
    out, original, capped = packager._truncate_conclusion("")
    assert out is None
    assert original == 0
    assert capped is False


def test_citation_token_first_author_and_year():
    article = {"authors": ["Marcin James P.", "Smith John"], "year": 2023}
    assert packager._citation_token(article) == ("Marcin", 2023)


def test_citation_token_returns_none_without_year():
    article = {"authors": ["Smith John"], "year": None}
    assert packager._citation_token(article) is None


def test_detect_cited_articles_matches_first_author_year():
    articles = [
        {"pmid": "1", "authors": ["Marcin James P."], "year": 2023, "title": "T1"},
        {"pmid": "2", "authors": ["Pires Ana"], "year": 2025, "title": "T2"},
        {"pmid": "3", "authors": ["Goetz Matthew"], "year": 2024, "title": "T3"},
    ]
    narrative = "Marcin 2023 found a risk ratio of 0.93. Pires 2025 reported lower transfer."
    cited = packager._detect_cited_articles(narrative, articles)
    assert cited == {"1", "2"}


def test_detect_cited_articles_handles_missing_year_gracefully():
    articles = [{"pmid": "1", "authors": ["Smith John"], "year": None, "title": "T"}]
    cited = packager._detect_cited_articles("Smith said hi.", articles)
    assert cited == set()


def test_assign_canonical_outcomes_picks_first():
    articles = [
        {"pmid": "1", "authors": ["Marcin"], "year": 2023, "title": "T1"},
        {"pmid": "2", "authors": ["Pires"], "year": 2025, "title": "T2"},
    ]
    by_outcome = [
        {"outcome": "Transfer rate", "narrative": "Marcin 2023 found 0.93. Pires 2025 reported."},
        {"outcome": "Length of stay", "narrative": "Marcin 2023 reduced LOS."},
        {"outcome": "Cost", "narrative": "Cost was favorable."},
    ]
    canonical = packager._assign_canonical_outcomes(articles, by_outcome)
    assert canonical["1"] == "Transfer rate"
    assert canonical["2"] == "Transfer rate"


def test_assign_canonical_outcomes_unmatched_returns_none():
    articles = [{"pmid": "9", "authors": ["Goetz"], "year": 2024, "title": "T"}]
    by_outcome = [{"outcome": "Transfer rate", "narrative": "Marcin said things."}]
    canonical = packager._assign_canonical_outcomes(articles, by_outcome)
    assert canonical.get("9") is None


def test_compute_also_discussed_lists_other_outcomes():
    articles = [{"pmid": "1", "authors": ["Marcin"], "year": 2023, "title": "T"}]
    by_outcome = [
        {"outcome": "Transfer rate", "narrative": "Marcin 2023 found 0.93."},
        {"outcome": "Length of stay", "narrative": "Marcin 2023 reduced LOS."},
        {"outcome": "Cost", "narrative": "Cost was favorable."},
    ]
    canonical = packager._assign_canonical_outcomes(articles, by_outcome)
    also = packager._compute_also_discussed(articles, by_outcome, canonical)
    assert also["1"] == ["Length of stay"]


def test_build_card_full_article():
    article = {
        "pmid": "1", "authors": ["Marcin James P.", "Smith John"], "year": 2023,
        "title": "Telemedicine reduces LOS in rural EDs",
        "journal": "Pediatrics", "volume": "150", "issue": "2", "pages": "e1-e10",
        "doi": "10.1/a", "study_design": "rct", "evidence_level": "II",
        "quality_score": 0.78, "quality_instrument": "RoB 2.0", "sample_size": 696,
        "tldr": "Telemedicine reduced transfer.",
        "intervention_details": "Real-time consult.", "comparison_details": "Phone consult.",
        "primary_outcomes": ["Transfer", "LOS"],
        "effect_sizes": "Transfer RR 0.93.",
        "author_conclusions": "Telemedicine reduces transfer.",
    }
    md, audit = packager._build_card(article, also_discussed_in=["Length of stay"])
    assert "Marcin JP, et al. (2023)." in md
    assert "Design: RCT" in md
    assert "TL;DR" in md
    assert "Real-time consult" in md
    assert "Phone consult" in md
    assert "Transfer" in md
    assert "Telemedicine reduces transfer" in md
    assert "Also discussed in: §Length of stay" in md
    assert audit["card_id"].startswith("card-marcin-2023")
    assert audit["fields_hidden"] == []
    assert audit["truncations"] == []


def test_build_card_records_hidden_fields():
    article = {
        "pmid": "3", "authors": ["Goetz Matthew"], "year": 2024,
        "title": "Editorial", "journal": "BMJ",
        "study_design": "narrative_review", "evidence_level": "IV",
    }
    md, audit = packager._build_card(article, also_discussed_in=[])
    # No TL;DR, no intervention, no findings, no conclusions
    assert "TL;DR" not in md
    assert "Intervention vs" not in md
    assert "Key findings" not in md
    assert "Author conclusion" not in md
    assert "Also discussed in" not in md
    assert "tldr" in audit["fields_hidden"]
    assert "intervention_details" in audit["fields_hidden"]
    assert "primary_outcomes" in audit["fields_hidden"]
    assert "author_conclusions" in audit["fields_hidden"]


def test_build_card_records_truncation():
    article = {
        "pmid": "5", "authors": ["Long Author"], "year": 2026,
        "title": "T", "journal": "J",
        "author_conclusions": " ".join(["word"] * 80),
    }
    _, audit = packager._build_card(article, also_discussed_in=[])
    assert audit["truncations"] == [
        {"field": "author_conclusions", "original_words": 80, "capped_words": 50}
    ]


def test_cover_page_renders_required_fields():
    state = {
        "review_id": "fake-2026", "review_type": "systematic",
        "raw_question": "Does telemedicine reduce LOS?",
    }
    cover = packager._build_cover_page(
        state, included_count=3, search_first="2026-05-01", search_last="2026-05-04",
        generated_date="2026-05-09",
    )
    assert "Does telemedicine reduce LOS?" in cover
    assert "Prepared using lit-review-agent" in cover
    assert "Systematic" in cover
    assert "2026-05-09" in cover
    assert "3 included articles" in cover
    assert "2026-05-01" in cover and "2026-05-04" in cover


def test_cover_page_uses_configured_author():
    page = packager._build_cover_page(
        {"raw_question": "Q", "review_type": "systematic"},
        included_count=3, search_first="2020", search_last="2024",
        generated_date="2026-07-02", report_author="Jane Smith, MD",
    )
    assert "Jane Smith, MD" in page
    assert "Dalton Haslam" not in page


def test_cover_page_default_author_when_blank():
    page = packager._build_cover_page(
        {"raw_question": "Q", "review_type": "systematic"},
        included_count=3, search_first="2020", search_last="2024",
        generated_date="2026-07-02", report_author="",
    )
    assert "Prepared using lit-review-agent" in page


def test_extract_executive_summary_sections_keeps_implications_and_conclusion():
    md = "# Executive Summary\n\n## Background\nbg\n\n## Clinical and Operational Implications\n- Expect reduction.\n\n## Conclusion\nIt works.\n\n---\n\n## Appendix — Supplementary Abstract-Only Evidence (Not Full-Text Appraised)\nstuff"
    closer = packager._build_conclusion_section(md)
    assert "Clinical and Operational Implications" in closer
    assert "Conclusion" in closer
    assert "Background" not in closer
    assert "Appendix" not in closer


def test_reference_list_alphabetical_and_numbered():
    articles = [
        {"authors": ["Pires Ana"], "year": 2025, "title": "Cost", "journal": "JAMA Pediatrics"},
        {"authors": ["Marcin James P.", "Smith John"], "year": 2023, "title": "T1",
         "journal": "Pediatrics", "volume": "150", "issue": "2", "pages": "e1-e10",
         "doi": "10.1/a"},
        {"authors": ["Goetz Matthew"], "year": 2024, "title": "Editorial", "journal": "BMJ"},
    ]
    refs = packager._build_reference_list(articles)
    assert refs.startswith("1. Goetz")  # alphabetical
    assert "2. Marcin" in refs
    assert "3. Pires" in refs
    assert "DOI: 10.1/a" in refs


def test_reference_list_handles_missing_authors():
    articles = [{"title": "Anonymous report", "year": 2024, "journal": "X"}]
    refs = packager._build_reference_list(articles)
    assert "Anonymous report" in refs


def test_findings_by_outcome_groups_canonical_cards():
    articles = [
        {"pmid": "1", "authors": ["Marcin James P."], "year": 2023, "title": "T1",
         "study_design": "rct", "evidence_level": "II",
         "tldr": "marcin tldr", "primary_outcomes": ["X"], "effect_sizes": "ES"},
        {"pmid": "2", "authors": ["Pires Ana"], "year": 2025, "title": "T2",
         "study_design": "retrospective_cohort", "evidence_level": "III-3",
         "tldr": "pires tldr", "primary_outcomes": ["X"], "effect_sizes": "ES"},
    ]
    by_outcome = [
        {"outcome": "Transfer", "narrative": "Marcin 2023 found 0.93. Pires 2025 reported."},
        {"outcome": "LOS", "narrative": "Marcin 2023 -2 days."},
    ]
    md, audit = packager._build_findings_by_outcome(by_outcome, articles)
    assert "### Transfer" in md
    assert "### LOS" in md
    assert "Marcin JP." in md
    assert "Pires A." in md
    # Each article should appear once
    assert md.count("Marcin JP.") == 1
    # Cross-reference appears under the non-canonical outcome
    assert "Also discussed in: §LOS" in md
    assert audit["cards_emitted"] == 2
    assert audit["cards_in_uncited_section"] == []


def test_findings_by_outcome_uncited_lands_in_z_section():
    articles = [
        {"pmid": "9", "authors": ["Goetz Matthew"], "year": 2024, "title": "Editorial",
         "study_design": "narrative_review", "evidence_level": "IV"},
    ]
    by_outcome = [{"outcome": "Transfer", "narrative": "No one cited Goetz."}]
    md, audit = packager._build_findings_by_outcome(by_outcome, articles)
    assert "Additional Included Studies" in md
    assert "Goetz M." in md
    assert "9" in audit["cards_in_uncited_section"]


def test_split_executive_summary_strips_appendix_heading():
    md = (
        "# Title\n\n"
        "## Background\nbg\n\n"
        "## Conclusion\ncon\n\n"
        "## Appendix — Supplementary Abstract-Only Evidence (Not Full-Text Appraised)\n"
        "body text"
    )
    main, appendix = packager._split_executive_summary(md)
    assert "## Background" in main
    assert "## Conclusion" in main
    assert "## Appendix" not in main
    # Appendix returned WITHOUT the inner H2 heading; outer cmd_assemble adds the H2
    assert "## Appendix" not in appendix
    assert "body text" in appendix


def test_split_executive_summary_no_duplication_when_leading_h2():
    # Claude's exec summary often begins directly with "## Background" (no H1 title). The
    # pre-H2 preamble slice must be empty in that case, or the first section is emitted twice.
    md = "## Background\n\nContext paragraph.\n\n## Methods\n\nMethods body."
    main, appendix = packager._split_executive_summary(md)
    assert main.count("## Background") == 1
    assert main.count("Context paragraph.") == 1


@pytest.mark.skipif(not shutil.which("pandoc"), reason="pandoc not installed")
def test_cover_page_newpage_is_real_page_break():
    # The cover page ends with a page break. _render passes `-f markdown-raw_tex`, which turns a
    # bare `\newpage` into the literal text "\textbackslash newpage". The break must be emitted
    # as a raw-latex block so it survives as a real \newpage command.
    cover = packager._build_cover_page(
        {"raw_question": "Q", "review_type": "systematic"},
        included_count=5, search_first=None, search_last=None, generated_date="2026-07-01",
    )
    latex = subprocess.run(
        ["pandoc", "-f", "markdown-raw_tex", "-t", "latex"],
        input=cover, capture_output=True, text=True,
    ).stdout
    assert "\\newpage" in latex
    assert "textbackslash" not in latex


def test_assemble_writes_package_and_audit(tmp_path):
    review_dir = _copy_fake_review(tmp_path)
    import src.skills.packager as packager
    rc = packager.cmd_assemble(review_dir)
    assert rc == 0
    package = (review_dir / "package.md").read_text()
    assert "Does telemedicine reduce hospital length of stay?" in package
    assert "Prepared using lit-review-agent" in package
    assert "## 1. Executive Summary" in package
    assert "## 2. Methods" in package
    assert "## 3. Findings by Outcome" in package
    assert "## 4. Evidence Quality Summary" in package
    assert "## 5. Gaps and Future Research" in package
    assert "## 6. Conclusion and Implications" in package
    assert "## Appendix B — Full Reference List" in package
    audit = json.loads((review_dir / "_package_audit.json").read_text())
    assert audit["review_slug"] == "fake-review-2026"
    assert audit["cards_emitted"] == 3


def test_assemble_idempotent(tmp_path):
    review_dir = _copy_fake_review(tmp_path)
    import src.skills.packager as packager
    packager.cmd_assemble(review_dir)
    first = (review_dir / "package.md").read_text()
    packager.cmd_assemble(review_dir)
    second = (review_dir / "package.md").read_text()
    # Modulo timestamps in the cover page, the assembled body should be equivalent.
    # Simple proxy: same length within 200 chars (timestamp difference negligible).
    assert abs(len(first) - len(second)) < 200


import pytest as _pytest


@_pytest.mark.skipif(not shutil.which("pandoc"), reason="pandoc not installed")
def test_render_writes_docx(tmp_path):
    review_dir = _copy_fake_review(tmp_path)
    import src.skills.packager as packager
    rc = packager.cmd_assemble(review_dir)
    assert rc == 0
    rc = packager.cmd_render(review_dir, format="docx")  # docx-only avoids xelatex dep in CI
    assert rc == 0
    docx = review_dir / "fake-review-2026_report.docx"
    assert docx.exists() and docx.stat().st_size > 100


def test_render_fails_when_package_md_missing(tmp_path):
    review_dir = _copy_fake_review(tmp_path)
    # Do NOT run --assemble; ensure no package.md exists.
    import src.skills.packager as packager
    # cmd_render must fail (raise SystemExit via die() OR return non-zero).
    try:
        rc = packager.cmd_render(review_dir, format="docx")
    except SystemExit as e:
        rc = e.code if e.code is not None else 1
    assert rc != 0


def test_finalize_updates_packaged_fields(tmp_path):
    review_dir = _copy_fake_review(tmp_path)
    import src.skills.packager as packager
    packager.cmd_assemble(review_dir)
    rc = packager.cmd_finalize(review_dir, pandoc_version="3.9.0.2",
                               artifacts=["package.md", "fake-review-2026_report.docx"])
    assert rc == 0
    state = json.loads((review_dir / "state.json").read_text())
    assert state["packaged"] is True
    assert state["packaged_pandoc_version"] == "3.9.0.2"
    assert "fake-review-2026_report.docx" in state["packaged_artifacts"]
    assert state["packaged_audit_summary"]["cards_emitted"] == 3


def test_finalize_does_not_touch_pipeline_keys(tmp_path):
    review_dir = _copy_fake_review(tmp_path)
    state_path = review_dir / "state.json"
    original = json.loads(state_path.read_text())
    import src.skills.packager as packager
    packager.cmd_assemble(review_dir)
    packager.cmd_finalize(review_dir, pandoc_version="3.9", artifacts=[])
    after = json.loads(state_path.read_text())
    for k in ("review_id", "raw_question", "status", "prisma_counts"):
        assert after.get(k) == original.get(k), f"{k} was modified"


def test_full_pipeline_against_fake_review(tmp_path):
    review_dir = _copy_fake_review(tmp_path)
    import src.skills.packager as packager
    assert packager.cmd_assemble(review_dir) == 0
    assert (review_dir / "package.md").exists()
    audit = json.loads((review_dir / "_package_audit.json").read_text())
    # Expected shape:
    assert "review_slug" in audit
    assert "cards_emitted" in audit
    assert "fields_hidden" in audit
    assert "truncations" in audit
    assert "synthesis_citations_unmatched" in audit
    assert "canonical_outcome_assignments" in audit
    assert "cards_in_uncited_section" in audit
    # Editorial in fixture has minimal fields → should hit fields_hidden
    goetz_card = "card-goetz-2024"
    assert goetz_card in audit["fields_hidden"]
    assert "tldr" in audit["fields_hidden"][goetz_card]
    # Marcin and Pires both cited under "Transfer rate"
    canonical = audit["canonical_outcome_assignments"]
    assert canonical["1"] == "Transfer rate"
    assert canonical["2"] == "Transfer rate"
    # Goetz cited by no outcome → uncited section
    assert "3" in audit["cards_in_uncited_section"]
