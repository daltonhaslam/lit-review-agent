"""Assembles a polished shareable PDF+DOCX from a completed lit-review.

Pure data layer; no LLM calls. Subcommands:
  --scan      list reviews where state.json.status == "complete"
  --validate  check source files + pandoc + xelatex
  --assemble  write package.md + _package_audit.json
  --render    render PDF + DOCX via pandoc
  --finalize  update state.json with packaged metadata
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.agent import state as state_manager  # noqa: E402
from src.integrations.config import load_api_config  # noqa: E402
from src.skills._base import die, prepare_output  # noqa: E402
from src.skills import _render  # noqa: E402


TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

REQUIRED_FILES = (
    "state.json",
    "articles_included.json",
    "synthesis.json",
    "executive_summary.md",
    "protocol.md",
    "search_log.md",
    "prisma_flow.md",
    "quality_report.md",
    "gap_analysis.md",
)


def _which(name: str) -> str | None:
    return shutil.which(name)


_SENT_END_RE = re.compile(r"(?<=[.!?])\s+")

_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)

_DESIGN_LABELS = {
    "rct": "RCT",
    "cluster_rct": "Cluster RCT",
    "crossover_rct": "Crossover RCT",
    "non_randomized_trial": "Non-randomized trial",
    "prospective_cohort": "Prospective cohort",
    "retrospective_cohort": "Retrospective cohort",
    "case_control": "Case-control",
    "case_series": "Case series",
    "case_report": "Case report",
    "cross_sectional": "Cross-sectional",
    "qualitative": "Qualitative",
    "mixed_methods": "Mixed methods",
    "systematic_review": "Systematic review",
    "meta_analysis": "Meta-analysis",
    "narrative_review": "Narrative review",
    "scoping_review": "Scoping review",
    "editorial": "Editorial / commentary",
    "guideline": "Clinical guideline",
}


def _abbrev_author(name: str) -> str:
    """'Marcin James P.' → 'Marcin JP'."""
    parts = name.strip().split()
    if not parts:
        return ""
    surname, *given = parts
    initials = "".join(p[0] for p in given if p and p[0].isalpha())
    return f"{surname} {initials}".strip()


def _reference_sort_key(article: dict) -> tuple[str, int]:
    authors = article.get("authors") or []
    # A non-empty authors list can hold an empty/whitespace name, so a bare
    # truthiness guard isn't enough — split() would yield [] and crash.
    parts = authors[0].strip().split() if authors else []
    surname = parts[0] if parts else "ZZZ"
    return surname.lower(), int(article.get("year") or 9999)


def _build_reference_list(articles: list[dict]) -> str:
    sorted_articles = sorted(articles, key=_reference_sort_key)
    lines = []
    for i, a in enumerate(sorted_articles, start=1):
        authors = "; ".join(_abbrev_author(x) for x in (a.get("authors") or [])) or "Anonymous"
        year = a.get("year") or "n.d."
        title = a.get("title") or "Untitled"
        journal = a.get("journal") or ""
        loc = ""
        if journal:
            loc = journal
            if a.get("volume"):
                loc += f" {a['volume']}"
                if a.get("issue"):
                    loc += f"({a['issue']})"
                if a.get("pages"):
                    loc += f":{a['pages']}"
        doi = f" DOI: {a['doi']}" if a.get("doi") else ""
        lines.append(f"{i}. {authors}. {title}. {loc} ({year}).{doi}".strip())
    return "\n".join(lines)


def _format_authors(authors: list[str]) -> str:
    if not authors:
        return ""
    if len(authors) == 1:
        return f"{_abbrev_author(authors[0])}."
    return f"{_abbrev_author(authors[0])}, et al."


def _build_citation_header(article: dict) -> str:
    author_str = _format_authors(article.get("authors") or [])
    year = article.get("year")
    title = (article.get("title") or "").strip()
    journal = (article.get("journal") or "").strip()
    volume = article.get("volume")
    issue = article.get("issue")
    pages = article.get("pages")
    doi = article.get("doi")

    line1_parts = []
    if author_str and year:
        # author_str already ends in '.' (single or "et al."); just append "(year)."
        line1_parts.append(f"**{author_str} ({year}).**")
    elif author_str:
        line1_parts.append(f"**{author_str}**")
    if title:
        line1_parts.append(f"*{title}.*")

    line2_parts = []
    if journal:
        loc = journal
        if volume:
            loc += f" {volume}"
            if issue:
                loc += f"({issue})"
            if pages:
                loc += f":{pages}"
        elif pages:
            loc += f" {pages}"
        line2_parts.append(f"{loc}.")
    if doi:
        line2_parts.append(f"DOI: {doi}")

    lines = []
    if line1_parts:
        lines.append(" ".join(line1_parts))
    if line2_parts:
        lines.append(" ".join(line2_parts))
    return "\n".join(lines)


def _format_quality(score, instrument) -> str | None:
    if score is None:
        return None
    s = f"{float(score):.2f}"
    return f"Quality: {s} ({instrument})" if instrument else f"Quality: {s}"


def _build_tldr(article: dict) -> str | None:
    tldr = (article.get("tldr") or "").strip()
    if tldr:
        return tldr
    abstract = (article.get("abstract") or "").strip()
    if not abstract:
        return None
    first = _SENT_END_RE.split(abstract, maxsplit=1)[0].strip()
    return first or None


def _build_meta_line(article: dict) -> str:
    bits = []
    design = article.get("study_design")
    if design:
        bits.append(f"Design: {_DESIGN_LABELS.get(design, design.replace('_', ' ').capitalize())}")
    level = article.get("evidence_level")
    if level:
        bits.append(f"Level: {level}")
    quality = _format_quality(article.get("quality_score"), article.get("quality_instrument"))
    if quality:
        bits.append(quality)
    n = article.get("sample_size")
    if n:
        bits.append(f"N = {n}")
    return "  •  ".join(bits)


def _build_key_findings(article: dict, *, cap: int = 4) -> list[str]:
    outcomes = list(article.get("primary_outcomes") or [])
    effect_sizes = (article.get("effect_sizes") or "").strip()
    if not outcomes and not effect_sizes:
        return []
    if len(outcomes) > cap - 1:
        kept = outcomes[: cap - 1]
        overflow = len(outcomes) - len(kept)
        kept.append(f"+{overflow} more outcomes (see evidence_table.csv)")
        return kept
    items = outcomes[:]
    if effect_sizes:
        items.append(effect_sizes)
    return items


def _article_id(article: dict) -> str:
    return article.get("pmid") or article.get("doi") or article.get("title", "")


def _citation_token(article: dict) -> tuple[str, int] | None:
    authors = article.get("authors") or []
    year = article.get("year")
    if not authors or not year:
        return None
    # First author may be an empty/whitespace string despite the list being
    # non-empty; treat that as un-citable rather than crashing.
    parts = authors[0].strip().split()
    if not parts:
        return None
    surname = parts[0]
    return surname, int(year)


def _detect_cited_articles(narrative: str, articles: list[dict]) -> set[str]:
    cited: set[str] = set()
    for a in articles:
        token = _citation_token(a)
        if token is None:
            continue
        surname, year = token
        pattern = re.compile(rf"\b{re.escape(surname)}\b[^.\n]{{0,40}}\b{year}\b")
        if pattern.search(narrative):
            cited.add(_article_id(a))
    return cited


# Best-effort audit signal. The regex misses compound surnames ("McDonald",
# "Van Halen", hyphenated) and apostrophe surnames ("O'Brien" matches partial
# "Brien"). False positives are bounded; false negatives are silent. Caller
# (cmd_assemble) emits these into _package_audit.json for human review.
_CITATION_TOKEN_RE = re.compile(
    r"\b([A-Z][a-z]+(?:\s+(?:and|&)\s+[A-Z][a-z]+)?)\s+(\d{4})\b"
)


def _detect_uncited_tokens(
    narrative: str, articles: list[dict], outcome: str | None = None
) -> list[dict]:
    """Return Surname-YYYY tokens in narrative that don't match any article.

    Each entry is {"token": "Brown 2019", "outcome": outcome_str_or_None}.
    Tokens are deduped within the narrative.
    """
    canonical: set[tuple[str, int]] = set()
    for a in articles:
        tok = _citation_token(a)
        if tok is not None:
            canonical.add((tok[0].lower(), tok[1]))

    seen: set[str] = set()
    unmatched: list[dict] = []
    for m in _CITATION_TOKEN_RE.finditer(narrative):
        surname_phrase = m.group(1)
        year = int(m.group(2))
        # Try first-word surname (handles "Smith and Jones 2020" → "Smith").
        # Suppression only applies when narrative lists the article's first
        # author first; reversed order ("Jones and Smith 2020" vs. article
        # authored ["Smith J", "Jones K"]) produces a false-positive token.
        first_surname = surname_phrase.split()[0].lower()
        if (first_surname, year) in canonical:
            continue
        token = f"{surname_phrase} {year}"
        if token in seen:
            continue
        seen.add(token)
        unmatched.append({"token": token, "outcome": outcome})
    return unmatched


def _assign_canonical_outcomes(
    articles: list[dict], by_outcome: list[dict]
) -> dict[str, str | None]:
    canonical: dict[str, str | None] = {}
    cited_per_outcome: list[set[str]] = [
        _detect_cited_articles(o.get("narrative", ""), articles) for o in by_outcome
    ]
    for a in articles:
        aid = _article_id(a)
        canonical[aid] = None
        for outcome, cited in zip(by_outcome, cited_per_outcome):
            if aid in cited:
                canonical[aid] = outcome["outcome"]
                break
    return canonical


def _compute_also_discussed(
    articles: list[dict], by_outcome: list[dict], canonical: dict[str, str | None]
) -> dict[str, list[str]]:
    also: dict[str, list[str]] = {}
    for a in articles:
        aid = _article_id(a)
        canon = canonical.get(aid)
        others = []
        for o in by_outcome:
            cited = _detect_cited_articles(o.get("narrative", ""), [a])
            if aid in cited and o["outcome"] != canon:
                others.append(o["outcome"])
        also[aid] = others
    return also


def _truncate_conclusion(text: str | None, *, cap: int = 50) -> tuple[str | None, int, bool]:
    text = (text or "").strip()
    if not text:
        return None, 0, False
    words = text.split()
    if len(words) <= cap:
        return text, len(words), False
    return " ".join(words[:cap]) + "…", len(words), True


def _card_id(article: dict) -> str:
    token = _citation_token(article)
    if token:
        surname, year = token
        return f"card-{surname.lower()}-{year}"
    return f"card-{_article_id(article).lower()}"


def _build_card(article: dict, *, also_discussed_in: list[str]) -> tuple[str, dict]:
    audit = {"card_id": _card_id(article), "fields_hidden": [], "truncations": []}
    lines: list[str] = []

    lines.append(f"<a id=\"{_card_id(article)}\"></a>")
    lines.append("")

    header = _build_citation_header(article)
    if header:
        lines.append(header)
        lines.append("")

    meta = _build_meta_line(article)
    if meta:
        lines.append(f"  {meta}")
        lines.append("")

    tldr = _build_tldr(article)
    if tldr:
        lines.append(f"▸ **TL;DR**  {tldr}")
        lines.append("")
    else:
        audit["fields_hidden"].append("tldr")

    intervention = (article.get("intervention_details") or "").strip()
    comparator = (article.get("comparison_details") or "").strip()
    if intervention or comparator:
        if intervention and comparator:
            lines.append(f"  **Intervention vs. comparator:** {intervention} *vs.* {comparator}")
        elif intervention:
            lines.append(f"  **Intervention:** {intervention}")
        else:
            lines.append(f"  **Comparator:** {comparator}")
        lines.append("")
    else:
        audit["fields_hidden"].append("intervention_details")

    findings = _build_key_findings(article)
    if findings:
        lines.append("  **Key findings:**")
        for f in findings:
            lines.append(f"  - {f}")
        lines.append("")
    else:
        audit["fields_hidden"].append("primary_outcomes")

    conclusion, original_words, capped = _truncate_conclusion(article.get("author_conclusions"))
    if conclusion:
        lines.append(f"  **Author conclusion:** {conclusion}")
        lines.append("")
        if capped:
            audit["truncations"].append({
                "field": "author_conclusions",
                "original_words": original_words,
                "capped_words": 50,
            })
    else:
        audit["fields_hidden"].append("author_conclusions")

    if also_discussed_in:
        joined = ", ".join(f"§{o}" for o in also_discussed_in)
        lines.append(f"  *Also discussed in: {joined}*")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n", audit


def _build_findings_by_outcome(
    by_outcome: list[dict], articles: list[dict]
) -> tuple[str, dict]:
    canonical = _assign_canonical_outcomes(articles, by_outcome)
    also = _compute_also_discussed(articles, by_outcome, canonical)

    audit = {
        "cards_emitted": 0,
        "cards_in_uncited_section": [],
        "card_audits": [],
        "canonical_outcome_assignments": canonical,
        "also_discussed_assignments": also,
    }

    out_lines = []
    by_index: dict[str, list[dict]] = {o["outcome"]: [] for o in by_outcome}
    uncited: list[dict] = []
    for a in articles:
        aid = _article_id(a)
        canon = canonical.get(aid)
        if canon and canon in by_index:
            by_index[canon].append(a)
        else:
            uncited.append(a)

    for o in by_outcome:
        out_lines.append(f"### {o['outcome']}")
        out_lines.append("")
        narrative = (o.get("narrative") or "").strip()
        if narrative:
            out_lines.append(narrative)
            out_lines.append("")
        for a in by_index[o["outcome"]]:
            md, card_audit = _build_card(a, also_discussed_in=also.get(_article_id(a), []))
            out_lines.append("---")
            out_lines.append("")
            out_lines.append(md)
            audit["cards_emitted"] += 1
            audit["card_audits"].append(card_audit)

    if uncited:
        out_lines.append("### Additional Included Studies")
        out_lines.append("")
        out_lines.append(
            "Articles included in the appraised set whose findings are not "
            "specifically cited in the by-outcome synthesis above."
        )
        out_lines.append("")
        for a in uncited:
            md, card_audit = _build_card(a, also_discussed_in=[])
            out_lines.append("---")
            out_lines.append("")
            out_lines.append(md)
            audit["cards_emitted"] += 1
            audit["cards_in_uncited_section"].append(_article_id(a))
            audit["card_audits"].append(card_audit)

    return "\n".join(out_lines), audit


def _split_markdown_sections(md: str) -> list[tuple[str, str]]:
    """Return list of (heading_text, body) for each `## ` section in order."""
    matches = list(_SECTION_RE.finditer(md))
    out = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md)
        out.append((m.group(1).strip(), md[start:end].strip()))
    return out


def _build_cover_page(
    state: dict, *, included_count: int, search_first: str | None,
    search_last: str | None, generated_date: str, report_author: str = "",
) -> str:
    title = state.get("raw_question") or state.get("review_id", "Literature Review")
    review_type = (state.get("review_type") or "review").capitalize()
    search_window = ""
    if search_first and search_last:
        search_window = f"Search dates: {search_first} → {search_last}"
    elif search_first:
        search_window = f"Search dates: {search_first}"
    lines = [
        "::: {.cover-page}",
        f"# {title}",
        "",
        f"**{report_author}**" if report_author else "**Prepared using lit-review-agent**",
        "",
        f"**{review_type} Review**",
        "",
        f"Generated: {generated_date}",
        "",
        f"{included_count} included articles",
        "",
        search_window,
        ":::",
        "",
        # Raw-latex block, not a bare `\newpage`: the render layer passes `-f markdown-raw_tex`
        # (injection safety), which turns a bare backslash command into literal text. A fenced
        # {=latex} raw block uses the separate raw_attribute extension and survives as a real break.
        "```{=latex}",
        "\\newpage",
        "```",
        "",
    ]
    return "\n".join(line for line in lines if line is not None)


def _build_conclusion_section(executive_summary_md: str) -> str:
    sections = _split_markdown_sections(executive_summary_md)
    keep = []
    for heading, body in sections:
        h = heading.lower()
        if h.startswith("clinical and operational implications") or h == "conclusion":
            keep.append(f"## {heading}\n\n{body}")
    return "\n\n".join(keep)


def cmd_validate(review_dir: Path) -> int:
    issues: list[str] = []
    state_path = review_dir / "state.json"
    if not state_path.exists():
        die(f"No state.json at {review_dir}")
    state = json.loads(state_path.read_text())
    if state.get("status") != "complete":
        issues.append(f"Review status is '{state.get('status')}', expected 'complete'.")

    # Detect quick-review folders. /lit-review-quick produces narrative reviews
    # without quality_report.md or gap_analysis.md.
    is_quick = (
        state.get("review_type") == "narrative"
        and not (review_dir / "quality_report.md").exists()
    )
    if is_quick:
        issues.append(
            "This review was produced by /lit-review-quick. v1 packager supports "
            "systematic / scoping reviews only."
        )

    for f in REQUIRED_FILES:
        if not (review_dir / f).exists():
            issues.append(f"Missing source file: {f}")

    if not _which("pandoc"):
        issues.append("pandoc not found. Install: brew install pandoc")
    if not _which("xelatex"):
        issues.append("xelatex not found. Install: brew install --cask basictex")

    if issues:
        print(prepare_output({"ok": False, "issues": issues}))
        return 1
    print(prepare_output({"ok": True, "review_dir": str(review_dir)}))
    return 0


def cmd_scan(reviews_dir: Path) -> int:
    rows = []
    if reviews_dir.exists():
        for entry in sorted(reviews_dir.iterdir()):
            if not entry.is_dir():
                continue
            state_file = entry / "state.json"
            if not state_file.exists():
                continue
            try:
                review = state_manager.load(entry)
            except Exception:
                continue
            if review.status.value != "complete":
                continue
            rows.append({
                "review_id": review.review_id,
                "review_type": review.review_type.value if review.review_type else None,
                "raw_question": review.raw_question,
                "packaged": getattr(review, "packaged", False),
                "packaged_at": review.packaged_at.isoformat() if review.packaged_at else None,
            })
    print(prepare_output({"reviews": rows}))
    return 0


def _read_text(path: Path) -> str:
    return path.read_text() if path.exists() else ""


def _split_executive_summary(md: str) -> tuple[str, str]:
    """Return (main, abstract_only_appendix). The appendix begins at the
    `## Appendix — Supplementary Abstract-Only Evidence` heading if present."""
    sections = _split_markdown_sections(md)
    main_parts: list[str] = []
    appendix_parts: list[str] = []
    in_appendix = False
    # Preserve the H1 + leading content before the first H2. Anchor on the actual first
    # `## ` section (via _SECTION_RE) rather than "\n## " — otherwise an exec summary that
    # begins directly with "## Background" (no H1/preamble) has empty preamble but the naive
    # split captures the whole first section, which is then re-emitted by the loop (duplicated).
    first_h2 = _SECTION_RE.search(md)
    pre_h2 = md[: first_h2.start()] if first_h2 else md
    main_parts.append(pre_h2.strip())
    for heading, body in sections:
        if heading.lower().startswith("appendix"):
            in_appendix = True
        if in_appendix:
            appendix_parts.append(body)
        else:
            main_parts.append(f"## {heading}\n\n{body}")
    return "\n\n".join(main_parts), "\n\n".join(appendix_parts)


def cmd_assemble(review_dir: Path) -> int:
    state = json.loads((review_dir / "state.json").read_text())
    articles = json.loads((review_dir / "articles_included.json").read_text())
    synthesis = json.loads((review_dir / "synthesis.json").read_text())
    exec_summary_md = _read_text(review_dir / "executive_summary.md")
    protocol_md = _read_text(review_dir / "protocol.md")
    search_log_md = _read_text(review_dir / "search_log.md")
    prisma_md = _read_text(review_dir / "prisma_flow.md")
    quality_md = _read_text(review_dir / "quality_report.md")
    gap_md = _read_text(review_dir / "gap_analysis.md")

    by_outcome = synthesis.get("narrative", {}).get("by_outcome", [])
    findings_md, findings_audit = _build_findings_by_outcome(by_outcome, articles)

    unmatched_citations: list[dict] = []
    for o in by_outcome:
        narrative = o.get("narrative") or ""
        if not narrative:
            continue
        unmatched_citations.extend(
            _detect_uncited_tokens(narrative, articles, outcome=o["outcome"])
        )

    generated_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cover = _build_cover_page(
        state, included_count=len(articles),
        search_first=None, search_last=None,
        generated_date=generated_date,
        report_author=load_api_config().report_author,
    )

    methods_section = "\n\n".join(s for s in [protocol_md, search_log_md, prisma_md] if s)

    # Trim the trailing abstract-only appendix from the executive summary;
    # we move it to Appendix A.
    exec_main, appendix_a = _split_executive_summary(exec_summary_md)

    parts = [
        cover,
        "## 1. Executive Summary\n\n" + exec_main,
        "## 2. Methods\n\n" + methods_section,
        "## 3. Findings by Outcome\n\n" + findings_md,
        "## 4. Evidence Quality Summary\n\n" + quality_md,
        "## 5. Gaps and Future Research\n\n" + gap_md,
        "## 6. Conclusion and Implications\n\n" + _build_conclusion_section(exec_summary_md),
    ]
    if appendix_a.strip():
        parts.append("## Appendix A — Supplementary Abstract-Only Evidence\n\n" + appendix_a)
    parts.append("## Appendix B — Full Reference List\n\n" + _build_reference_list(articles))

    package_md = "\n\n".join(parts) + "\n"
    (review_dir / "package.md").write_text(package_md)

    audit = {
        "review_slug": state.get("review_id"),
        "assembled_at": datetime.now(timezone.utc).isoformat(),
        "cards_emitted": findings_audit["cards_emitted"],
        "cards_skipped": [],
        "fields_hidden": {
            c["card_id"]: c["fields_hidden"]
            for c in findings_audit["card_audits"] if c["fields_hidden"]
        },
        "truncations": [
            {**t, "card": c["card_id"]}
            for c in findings_audit["card_audits"]
            for t in c["truncations"]
        ],
        "synthesis_citations_unmatched": unmatched_citations,
        "canonical_outcome_assignments": findings_audit["canonical_outcome_assignments"],
        "cards_in_uncited_section": findings_audit["cards_in_uncited_section"],
    }
    (review_dir / "_package_audit.json").write_text(json.dumps(audit, indent=2))

    print(prepare_output({
        "ok": True,
        "package_md": str(review_dir / "package.md"),
        "audit": str(review_dir / "_package_audit.json"),
        "cards_emitted": audit["cards_emitted"],
    }))
    return 0


def cmd_finalize(review_dir: Path, *, pandoc_version: str | None,
                 artifacts: list[str]) -> int:
    state_path = review_dir / "state.json"
    state = json.loads(state_path.read_text())
    audit = json.loads((review_dir / "_package_audit.json").read_text())
    state["packaged"] = True
    state["packaged_at"] = datetime.now(timezone.utc).isoformat()
    state["packaged_artifacts"] = artifacts
    state["packaged_pandoc_version"] = pandoc_version
    state["packaged_audit_summary"] = {
        "cards_emitted": audit.get("cards_emitted", 0),
        "cards_with_warnings": len(audit.get("fields_hidden", {})),
        "synthesis_citations_unmatched": len(audit.get("synthesis_citations_unmatched", [])),
    }
    state_path.write_text(json.dumps(state, indent=2))
    print(prepare_output({"ok": True, "state": str(state_path)}))
    return 0


def cmd_render(review_dir: Path, *, format: str = "both") -> int:
    package_md = review_dir / "package.md"
    if not package_md.exists():
        die(f"package.md not found in {review_dir}. Run --assemble first.")
    slug = json.loads((review_dir / "state.json").read_text()).get("review_id", review_dir.name)
    out_pdf = review_dir / f"{slug}_report.pdf"
    out_docx = review_dir / f"{slug}_report.docx"
    template_pdf = TEMPLATES_DIR / "package_latex.tex"
    template_docx = TEMPLATES_DIR / "package_reference.docx"

    errors = []
    if format in ("pdf", "both"):
        rc, stderr = _render.render_pdf(package_md, out_pdf, template_pdf)
        if rc != 0:
            errors.append(("pdf", stderr))
    if format in ("docx", "both"):
        rc, stderr = _render.render_docx(package_md, out_docx, template_docx)
        if rc != 0:
            errors.append(("docx", stderr))

    if errors:
        for kind, stderr in errors:
            print(f"render {kind} failed: {stderr}", file=sys.stderr)
        return 1
    print(prepare_output({
        "ok": True,
        "pandoc_version": _render.pandoc_version(),
        "artifacts": [str(out_pdf) if format in ("pdf", "both") else None,
                      str(out_docx) if format in ("docx", "both") else None],
    }))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="packager")
    parser.add_argument("--scan", metavar="REVIEWS_DIR")
    parser.add_argument("--validate", metavar="REVIEW_DIR")
    parser.add_argument("--assemble", metavar="REVIEW_DIR")
    parser.add_argument("--render", metavar="REVIEW_DIR")
    parser.add_argument("--format", choices=("pdf", "docx", "both"), default="both")
    parser.add_argument("--finalize", metavar="REVIEW_DIR")
    parser.add_argument("--pandoc-version", default=None)
    parser.add_argument("--artifact", action="append", default=[])
    args = parser.parse_args(argv)

    if args.scan:
        return cmd_scan(Path(args.scan))
    if args.validate:
        return cmd_validate(Path(args.validate))
    if args.assemble:
        return cmd_assemble(Path(args.assemble))
    if args.render:
        return cmd_render(Path(args.render), format=args.format)
    if args.finalize:
        return cmd_finalize(Path(args.finalize),
                            pandoc_version=args.pandoc_version,
                            artifacts=args.artifact)
    parser.print_usage()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
