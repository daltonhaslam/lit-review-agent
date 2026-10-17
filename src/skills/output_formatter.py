"""Output formatter — generates all 9 review artifacts.

--prepare: loads synthesis.json + state, formats context for executive summary generation.
--save:    accepts Claude's executive summary text, writes all 9 artifacts; status=complete.

Artifacts:
  executive_summary.md  — written from Claude's output
  evidence_table.csv    — confirms existing; re-generates if missing
  prisma_flow.md        — PRISMA 2020 flow with all stage counts
  citations.ris         — Zotero-compatible RIS for included articles
  citations.bib         — BibTeX for included articles
  search_log.md         — confirms existing; stubs if missing
  retrieval_list.csv    — confirms existing
  gap_analysis.md       — confirms existing
  protocol.md           — confirms existing

Input:  review_dir (reads synthesis.json + state; status must be synthesized)
Output: all 9 artifacts; status=complete
CLI:
  python -m src.skills.output_formatter --review-dir reviews/{slug} --prepare
  python -m src.skills.output_formatter --review-dir reviews/{slug} --save --input-json '{...}'
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.agent import state as state_manager
from src.models.article import Article
from src.models.review import Review, ReviewStatus
from src.skills._base import die, parse_save_input, prepare_output
from src.skills.abstract_screener import BATCH_SIZE_DEFAULT as SCREEN_BATCH
from src.skills.data_extractor import BATCH_SIZE_DEFAULT as EXTRACT_BATCH
from src.skills.data_extractor import _write_evidence_table
from src.skills.multi_source_retriever import _DB_DISPLAY_NAMES, DEFAULT_SOURCES
from src.skills.quality_appraiser import BATCH_SIZE_DEFAULT as APPRAISE_BATCH

_EXPECTED_ARTIFACTS = [
    "executive_summary.md",
    "evidence_table.csv",
    "prisma_flow.md",
    "citations.ris",
    "citations.bib",
    "search_log.md",
    "retrieval_list.csv",
    "gap_analysis.md",
    "protocol.md",
    "ai_disclosure.md",
]

_EXECUTIVE_SUMMARY_TASK = (
    "You are an academic physician writing an executive summary of a systematic literature review "
    "for a hospital quality committee or C-suite audience. "
    "Write in clear, precise clinical language. Avoid jargon without definition. "
    "Structure: Background → PICO → Methods summary → Key findings by outcome → "
    "Evidence quality → Gaps → Clinical/operational implications → Conclusion. "
    "Use author-year inline citations exactly as provided (e.g., Smith 2022). "
    "Length: 600–1200 words. Produce only the markdown document — no preamble, no commentary."
)


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _write_prisma_flow(review: Review, output_path: Path, articles: list[Article]) -> None:
    p = review.prisma_counts
    # articles_included.json is the authoritative included set (both streams, post-full-text).
    # p.included already contains citation-chased survivors (CC runs at Stage 8.5, before
    # full-text), so the total must NOT re-add citation_chasing_added (the abstract-level count).
    total_included = len(articles)
    cc_included = sum(1 for a in articles if a.citation_chased)
    iteration_included = sum(1 for a in articles if a.iteration_added)
    primary_included = total_included - cc_included - iteration_included
    # Reports actually assessed at full-text. Fall back for pre-fix reviews whose state predates
    # the fulltext_assessed/not_retrieved fields (there eligibility held the assessed count).
    fulltext_assessed = p.fulltext_assessed or max(0, p.eligibility - p.not_retrieved)
    now = _today()
    excluded_screening = p.excluded_screening or {}
    excluded_fulltext = p.excluded_fulltext or {}
    cc_excluded = max(0, p.citation_chasing_screened - p.citation_chasing_added)

    lines = [
        "# PRISMA 2020 Flow Diagram",
        "",
        f"**Review:** {review.raw_question}",
        f"**Date:** {now}",
        "",
        "---",
        "",
        "## Identification",
        "",
        "### Stream 1: Databases and Registers",
        f"- Records identified via database searching: **{p.identified}**",
        f"  - Sources: {', '.join(review.sources_searched) or 'PubMed, OpenAlex, Semantic Scholar'}",
        f"- Duplicate records removed: {max(0, p.identified - p.screened)}",
        "",
        "### Stream 2: Citation Searching",
        f"- Records identified via citation searching: **{p.citation_chasing_identified}**",
        f"  - (backward: reference lists + forward: citing papers)",
        f"- Screened after deduplication: {p.citation_chasing_screened}",
        f"- Excluded at citation-chase screening: {cc_excluded}",
        f"- Added from citation searching: **{p.citation_chasing_added}**",
        "",
        "## Screening (Stream 1)",
        "",
        f"- Records screened (title/abstract): **{p.screened}**",
        f"- Records excluded at screening: **{sum(excluded_screening.values())}**",
    ]

    if excluded_screening:
        for reason, count in sorted(excluded_screening.items(), key=lambda x: -x[1]):
            lines.append(f"  - {reason}: {count}")

    lines += [
        "",
        "## Eligibility",
        "",
        f"- Reports sought for full-text retrieval: **{p.eligibility}**",
        f"  - (includes articles from both streams)",
        f"- Reports not retrieved (no PDF available): **{p.not_retrieved}**",
        f"- Reports assessed for eligibility: **{fulltext_assessed}**",
        f"- Articles excluded at full-text: **{sum(excluded_fulltext.values())}**",
    ]

    if excluded_fulltext:
        for reason, count in sorted(excluded_fulltext.items(), key=lambda x: -x[1]):
            lines.append(f"  - {reason}: {count}")

    lines += [
        "",
        "## Included",
        "",
        f"- Studies included from database searching: **{primary_included}**",
        f"- Studies added via citation searching: **{cc_included}**",
        *([f"- Studies added via iteration searching: **{iteration_included}**"] if iteration_included else []),
        f"- **Total studies included in review: {total_included}**",
        "",
        "---",
        "",
        "```",
        "IDENTIFICATION",
        "  Stream 1 (Databases/Registers)     Stream 2 (Citation Searching)",
        f"  Records identified: {p.identified:<12}   Identified: {p.citation_chasing_identified}",
        f"  Duplicates removed: {max(0, p.identified - p.screened):<12}   Screened: {p.citation_chasing_screened}",
        f"                                       Excluded: {cc_excluded}",
        f"                                       Added: {p.citation_chasing_added}",
        "        |                                    |",
        "        +-------- MERGED AT RETRIEVAL -------+",
        "                          |",
        "SCREENING (Stream 1)",
        f"  Records screened: {p.screened}",
        f"  Records excluded: {sum(excluded_screening.values())}",
        "        |",
        "ELIGIBILITY (both streams combined)",
        f"  Sought for retrieval: {p.eligibility}",
        f"  Not retrieved (no PDF): {p.not_retrieved}",
        f"  Assessed: {fulltext_assessed}",
        f"  Excluded: {sum(excluded_fulltext.values())}",
        "        |",
        "INCLUDED",
        f"  Database searching: {primary_included}",
        f"  Citation searching: {cc_included}",
        *([f"  Iteration searching: {iteration_included}"] if iteration_included else []),
        f"  Total included: {total_included}",
        "```",
    ]

    output_path.write_text("\n".join(lines), encoding="utf-8")


def _write_ris(articles: list[Article], output_path: Path) -> None:
    lines = []
    for a in articles:
        lines.append("TY  - JOUR")
        lines.append(f"TI  - {a.title}")
        for author in a.authors[:10]:
            lines.append(f"AU  - {author}")
        if a.year:
            lines.append(f"PY  - {a.year}")
        if a.journal:
            lines.append(f"JO  - {a.journal}")
        if a.volume:
            lines.append(f"VL  - {a.volume}")
        if a.issue:
            lines.append(f"IS  - {a.issue}")
        if a.pages:
            lines.append(f"SP  - {a.pages}")
        if a.doi:
            lines.append(f"DO  - {a.doi}")
        if a.pmid:
            lines.append(f"AN  - {a.pmid}")
        if a.abstract:
            lines.append(f"AB  - {a.abstract[:1000]}")
        lines.append("ER  - ")
        lines.append("")
    output_path.write_text("\n".join(lines), encoding="utf-8")


def _bib_key(article: Article, index: int) -> str:
    first_author = ""
    if article.authors:
        first_author = re.sub(r"[^a-zA-Z]", "", article.authors[0].split()[0]).lower()
    year = str(article.year) if article.year else ""
    first_word = ""
    if article.title:
        words = [w for w in re.sub(r"[^a-zA-Z ]", "", article.title).split() if len(w) > 3]
        first_word = words[0].lower() if words else ""
    return f"{first_author}{year}{first_word}" or f"article{index}"


def _write_bibtex(articles: list[Article], output_path: Path) -> None:
    lines = []
    for i, a in enumerate(articles, 1):
        key = _bib_key(a, i)
        lines.append(f"@article{{{key},")
        lines.append(f"  title = {{{a.title}}},")
        if a.authors:
            authors_str = " and ".join(a.authors[:10])
            lines.append(f"  author = {{{authors_str}}},")
        if a.year:
            lines.append(f"  year = {{{a.year}}},")
        if a.journal:
            lines.append(f"  journal = {{{a.journal}}},")
        if a.volume:
            lines.append(f"  volume = {{{a.volume}}},")
        if a.issue:
            lines.append(f"  number = {{{a.issue}}},")
        if a.pages:
            lines.append(f"  pages = {{{a.pages}}},")
        if a.doi:
            lines.append(f"  doi = {{{a.doi}}},")
        if a.pmid:
            lines.append(f"  note = {{PMID: {a.pmid}}},")
        lines.append("}")
        lines.append("")
    output_path.write_text("\n".join(lines), encoding="utf-8")


def _format_abstract_supplement_appendix(supplement_summary: str) -> str:
    return (
        "\n\n---\n\n"
        "## Appendix — Supplementary Abstract-Only Evidence (Not Full-Text Appraised)\n\n"
        "The following articles passed title/abstract screening but were not retrieved at "
        "full text and therefore were not appraised, extracted, or included in the main "
        "evidence base. They are presented for completeness only. **Findings are based on "
        "abstracts alone and may not survive full-text review — do not treat them as part "
        "of the appraised evidence.**\n\n"
        f"{supplement_summary.strip()}\n"
    )


def _write_if_missing(path: Path, content: str) -> None:
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def _stub_search_log(review_dir: Path) -> None:
    _write_if_missing(
        review_dir / "search_log.md",
        "# Search Log\n\n_No search log entries recorded. Re-run retrieval stages to populate._\n",
    )


def _stub_retrieval_list(review_dir: Path) -> None:
    _write_if_missing(
        review_dir / "retrieval_list.csv",
        "title,authors,year,journal,doi,pmid,oa_status,oa_pdf_url,notes\n",
    )


def _write_ai_disclosure(review: Review, review_dir: Path) -> None:
    now = _today()
    review_type = review.review_type.value if review.review_type else "systematic"
    sources = ", ".join(
        _DB_DISPLAY_NAMES.get(s, s) for s in (review.sources_searched or DEFAULT_SOURCES)
    )

    lines = [
        "# AI-Assisted Review Disclosure (PRISMA-trAIce)",
        "",
        "**Tool:** Claude (Anthropic), via Claude Code and the lit-review-agent skill",
        f"**Review type:** {review_type}",
        f"**Completed:** {now}",
        "",
        "## AI-Assisted Stages",
        "",
        "- Stage 1: Review type classification",
        "- Stage 2: PICO formulation",
        "- Stage 3: Protocol generation",
        "- Stage 4: Query construction",
        f"- Stage 8: Abstract screening (batched, {SCREEN_BATCH} articles per batch)",
        "- Stage 10: Full-text eligibility assessment",
        f"- Stage 11: Data extraction (batched, {EXTRACT_BATCH} articles per batch)",
        f"- Stage 13: Quality appraisal (batched, {APPRAISE_BATCH} articles per batch)",
        "- Stage 8.5: Citation chasing — candidate screening",
        "- Stage 15: Gap analysis",
        "- Stage 16: Evidence synthesis and executive summary",
        "",
        "## Automated Stages (no LLM)",
        "",
        f"- Stage 5: Database retrieval (API calls — {sources})",
        "- Stage 6: DOI resolution via CrossRef",
        "- Stage 7: Deduplication (DOI → title similarity → author+year fingerprint)",
        "- Stage 9.5: PDF download (open-access waterfall: existing OA link → Unpaywall → "
        "OpenAlex → PubMed Central → Europe PMC → Crossref)",
        "- Stage 12: Enrichment (Semantic Scholar / OpenAlex — citation counts, TLDRs, OA status)",
        "- Stage 14: Evidence level classification (rule-based NHMRC I–IV)",
        "",
        "## Human Review Points",
        "",
        "- Checkpoint 1: PICO approval",
        "- Checkpoint 2: Protocol approval and lock",
        "- Checkpoint 3: Post-retrieval / borderline article review",
        "- Checkpoint 4: Library retrieval break (library request for articles without OA PDFs)",
        "- Checkpoint 5: Included set approval",
        "- Checkpoint 6: Iteration decision",
        "- Final review: Executive summary and output package approval",
        "",
        "## Limitations",
        "",
        "- Screening decisions made by a single AI reviewer; "
        "borderline cases are escalated to human review at Checkpoint 5 (full-text stage)",
        "- Full-text assessment limited to articles with PDF available; "
        "articles without PDF are assessed on abstract only",
        "- Effect size extraction relies on abstract-level data for articles without PDF",
        "- AI screening decisions are not independently verifiable from the published artifact; "
        "decisions and rationales are stored in `articles_screened.json`",
        "- Citation-chased articles (added at Stage 8.5) may have incomplete metadata "
        "compared to database-retrieved articles",
    ]

    (review_dir / "ai_disclosure.md").write_text("\n".join(lines), encoding="utf-8")


_RUNLOG_MARKER = "<!-- New entries go here -->"


def _repo_root() -> Path:
    return Path(__file__).parent.parent.parent


def _default_runlog_path() -> Path:
    return _repo_root() / "RUNLOG.md"


def _extract_session_notes(block: str) -> str:
    """Return the lines under a '### Session notes' heading within a RUNLOG block."""
    marker = "### Session notes"
    if marker not in block:
        return ""
    return block.split(marker, 1)[1].lstrip("\n").rstrip()


def _runlog_completion_block(
    review: Review,
    present: list[str],
    total_included: int,
    date: str,
    issues: str,
    notes: str,
    session_notes: str,
) -> str:
    p = review.prisma_counts
    review_type = review.review_type.value if review.review_type else "systematic"
    checklist = " | ".join(
        f"{Path(a).stem} {'✓' if a in present else '✗'}" for a in _EXPECTED_ARTIFACTS
    )
    lines = [
        f"## {date} — {review.review_id} [COMPLETE]",
        "",
        f"**Question:** {review.raw_question}",
        f"**Review type:** {review_type} | **Iterations:** {review.iterations} | **Completed:** {date}",
        "",
        f"**PRISMA:** Identified {p.identified} | Screened {p.screened} | "
        f"Full-text assessed {p.eligibility} | Included {total_included} | "
        f"Via citation searching: {p.citation_chasing_added}",
        f"**Artifacts:** {checklist}",
        "",
        f"**Issues filed:** {issues}",
        f"**Notes:** {notes}",
        "",
        "### Session notes",
    ]
    block = "\n".join(lines) + "\n"
    if session_notes.strip():
        block += session_notes.rstrip() + "\n"
    return block


def _runlog_block_span(content: str, review_id: str) -> tuple[int, int] | None:
    """Return (start, end) of this review's existing RUNLOG block, or None.

    Matches the two heading forms the skill actually writes — `## {review_id}` (IN PROGRESS)
    and `## {date} — {review_id} [COMPLETE]` — anchoring on the whole review_id token so a
    review whose id is a substring of another's (e.g. `chf-2026` vs `chf-2026-pilot`) can never
    clobber the wrong block. The block runs to the next `## ` heading or EOF.
    """
    rid = re.escape(review_id)
    heading_re = re.compile(
        rf"^## (?:\d{{4}}-\d{{2}}-\d{{2}} — )?{rid}(?: \[COMPLETE\])?\s*$", re.MULTILINE
    )
    m = heading_re.search(content)
    if not m:
        return None
    next_m = re.compile(r"^## ", re.MULTILINE).search(content, m.end())
    return m.start(), (next_m.start() if next_m else len(content))


def _write_runlog_completion(
    review: Review,
    runlog_path: Path,
    present: list[str],
    total_included: int,
    issues: str = "none",
    notes: str = "",
) -> None:
    """Write/replace the review's RUNLOG [COMPLETE] block idempotently (opt-in via --runlog).

    Finds any existing block for this review (IN PROGRESS or COMPLETE), preserves its
    `### Session notes`, and replaces it with a completion block; otherwise inserts a new block
    immediately after the marker (newest-first). Atomic temp+replace so a crash can't truncate
    the shared log. Path defaults to repo-root RUNLOG.md, never the cwd.
    """
    if runlog_path.exists():
        content = runlog_path.read_text(encoding="utf-8")
    else:
        content = f"# Run Log\n\n{_RUNLOG_MARKER}\n"

    span = _runlog_block_span(content, review.review_id)
    session_notes = _extract_session_notes(content[span[0]:span[1]]) if span else ""
    block = _runlog_completion_block(
        review, present, total_included, _today(), issues, notes, session_notes
    )
    if span:
        content = content[:span[0]] + block + "\n" + content[span[1]:].lstrip("\n")
    elif _RUNLOG_MARKER in content:
        content = content.replace(_RUNLOG_MARKER, f"{_RUNLOG_MARKER}\n\n{block}", 1)
    else:
        content = content.rstrip() + "\n\n" + block

    tmp = runlog_path.with_suffix(runlog_path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(runlog_path)


def _recompute_prisma_from_articles(review_dir: Path, review: Review) -> None:
    """Recompute PRISMA exclusion counts from actual article files to fix stale state."""
    screened_path = review_dir / review.articles_screened_file
    if screened_path.exists():
        screened = json.loads(screened_path.read_text())
        excluded_screening: dict[str, int] = {}
        for a in screened:
            if a.get("screening_decision") == "exclude":
                reason = a.get("screening_reason_category") or "unspecified"
                excluded_screening[reason] = excluded_screening.get(reason, 0) + 1
        review.prisma_counts.excluded_screening = excluded_screening

    if not review.prisma_counts.excluded_fulltext:
        eligibility = review.prisma_counts.eligibility
        included = review.prisma_counts.included
        if eligibility > included:
            review.prisma_counts.excluded_fulltext = {
                "excluded at full-text assessment": eligibility - included
            }


def prepare(review_dir: Path) -> None:
    review = state_manager.load(review_dir)
    if review.status != ReviewStatus.synthesized:
        die(f"Expected status 'synthesized', got '{review.status}'.")

    synthesis_path = review_dir / "synthesis.json"
    if not synthesis_path.exists():
        die("synthesis.json not found — run synthesizer first.")
    synthesis = json.loads(synthesis_path.read_text())

    _recompute_prisma_from_articles(review_dir, review)
    state_manager.save(review, review_dir)

    pico = review.pico
    pico_text = (
        f"P: {pico.P} | I: {pico.I} | C: {pico.C} | O: {', '.join(pico.O)}"
        if pico else "PICO not defined"
    )

    prisma = review.prisma_counts
    review_type = review.review_type.value if review.review_type else "systematic"
    total_included = synthesis.get("total_included", prisma.included)
    narrative = synthesis.get("narrative", {})

    print(prepare_output({
        "review_id": review.review_id,
        "review_question": review.raw_question,
        "review_type": review_type,
        "pico_text": pico_text,
        "search_summary": {
            "databases": list(review.sources_searched) or ["PubMed", "OpenAlex", "Semantic Scholar"],
            "identified": prisma.identified,
            "screened": prisma.screened,
            "eligibility": prisma.eligibility,
            "included": prisma.included,
            "citation_chasing_added": prisma.citation_chasing_added,
            "total_included": total_included,
        },
        "key_findings": narrative.get("key_findings", []),
        "overall_narrative": narrative.get("overall_narrative", ""),
        "by_outcome": narrative.get("by_outcome", []),
        "evidence_quality": narrative.get("evidence_quality_summary", ""),
        "limitations": narrative.get("limitations", []),
        "executive_summary_task": _EXECUTIVE_SUMMARY_TASK,
    }))


def save(
    review_dir: Path,
    input_json: str,
    runlog: bool = False,
    runlog_path: Path | None = None,
    runlog_issues: str = "none",
    runlog_notes: str = "",
) -> None:
    data = parse_save_input(input_json)
    review = state_manager.load(review_dir)

    # Recompute PRISMA exclusion counts from the article files (mirrors prepare()) so the
    # terminal artifacts + RUNLOG publish correct numbers even on a direct --save or a
    # stale-state resume, rather than persisting whatever counts state.json happens to hold.
    _recompute_prisma_from_articles(review_dir, review)

    exec_summary = data.get("executive_summary", "")
    supplement = json.loads((review_dir / "synthesis.json").read_text()).get("abstract_supplement")
    if supplement:
        exec_summary = exec_summary.rstrip() + _format_abstract_supplement_appendix(supplement)
    (review_dir / "executive_summary.md").write_text(exec_summary, encoding="utf-8")

    included_path = review_dir / review.articles_included_file
    articles = [Article(**a) for a in json.loads(included_path.read_text())]

    _write_prisma_flow(review, review_dir / "prisma_flow.md", articles)
    _write_ris(articles, review_dir / "citations.ris")
    _write_bibtex(articles, review_dir / "citations.bib")

    if not (review_dir / "evidence_table.csv").exists():
        _write_evidence_table(articles, review_dir / "evidence_table.csv")

    _stub_search_log(review_dir)
    _stub_retrieval_list(review_dir)
    _write_ai_disclosure(review, review_dir)

    present, missing = [], []
    for artifact in _EXPECTED_ARTIFACTS:
        (present if (review_dir / artifact).exists() else missing).append(artifact)

    review.status = ReviewStatus.complete
    state_manager.save(review, review_dir)

    runlog_written = None
    if runlog:
        target = runlog_path or _default_runlog_path()
        _write_runlog_completion(
            review, target, present, len(articles), runlog_issues, runlog_notes
        )
        runlog_written = str(target)

    print(prepare_output({
        "artifacts_written": len(present),
        "artifacts_missing": missing,
        "included_articles": len(articles),
        "executive_summary_words": len(exec_summary.split()),
        "runlog_written": runlog_written,
    }))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Format all output artifacts for a completed review")
    parser.add_argument("--review-dir", required=True, metavar="DIR")
    parser.add_argument("--prepare", dest="mode_prepare", action="store_true")
    parser.add_argument("--save", dest="mode_save", action="store_true")
    parser.add_argument("--input-json", default="")
    parser.add_argument("--runlog", action="store_true",
                        help="Write/replace the review's RUNLOG [COMPLETE] block (opt-in closeout).")
    parser.add_argument("--runlog-path", default="",
                        help="RUNLOG path (default: repo-root RUNLOG.md).")
    parser.add_argument("--runlog-issues", default="none",
                        help="Issues-filed line for the RUNLOG entry.")
    parser.add_argument("--runlog-notes", default="",
                        help="Notes line for the RUNLOG entry.")
    args = parser.parse_args()

    review_dir = Path(args.review_dir)
    try:
        if args.mode_prepare:
            prepare(review_dir)
        elif args.mode_save:
            save(
                review_dir,
                args.input_json,
                runlog=args.runlog,
                runlog_path=Path(args.runlog_path) if args.runlog_path else None,
                runlog_issues=args.runlog_issues,
                runlog_notes=args.runlog_notes,
            )
        else:
            die("Specify --prepare or --save")
    except SystemExit:
        raise
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)
