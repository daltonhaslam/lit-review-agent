"""Full-text eligibility assessment for included + borderline articles.

--prepare: loads eligible articles, extracts PDF text, formats for Claude.
--save:    applies Claude's per-article decisions, writes articles_included.json.

Processes articles from both the primary database search and citation chasing in a
single pass (citation-chased includes are in articles_screened.json after Stage 8.5).

Input:  review_dir (reads articles_screened.json; status must be retrieval_list_generated or pdfs_downloaded)
        pdf_dir (default: review_dir/pdfs/)
Output: articles_included.json; prisma_counts.eligibility + .included updated;
        status=fulltext_assessed
CLI:
  python -m src.skills.fulltext_assessor --review-dir reviews/{slug} --prepare
  python -m src.skills.fulltext_assessor --review-dir reviews/{slug} --prepare --pdf-dir /path/to/pdfs
  python -m src.skills.fulltext_assessor --review-dir reviews/{slug} --save --input-json '{"decisions": [...]}'
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.agent import state as state_manager
from src.models.article import Article, ScreeningDecision
from src.models.review import ReviewStatus
from src.skills._base import die, iter_pdfs, parse_save_input, prepare_output, slice_bounds, validate_slice_spec
from src.skills._study_design import assign_study_design
from src.skills.abstract_screener import _build_pico_context
from src.utils.filename_utils import _normalize_doi_for_filename

try:
    import pypdf
    _PYPDF_AVAILABLE = True
except ImportError:
    _PYPDF_AVAILABLE = False

# Head+tail extraction (#63): the first N chars capture Title/Abstract/Intro/Methods; the last M
# capture Discussion/Limitations/Conclusion/Acknowledgments/Funding/COI — all systematically past
# a first-N-only cap. FULLTEXT_MAX_CHARS stays the exported symbol (data_extractor imports it).
FULLTEXT_HEAD_CHARS = 10000
FULLTEXT_TAIL_CHARS = 6000
FULLTEXT_MAX_CHARS = FULLTEXT_HEAD_CHARS + FULLTEXT_TAIL_CHARS  # 16000; ~4000 tokens

ASSESSMENT_TASK = (
    "Assess each article for full-text eligibility against the PICO(S) criteria and "
    "inclusion/exclusion criteria shown above. For each article where `fulltext` is not null, "
    "make a decision:\n"
    "- include: meets all inclusion criteria, triggers no exclusion criteria\n"
    "- exclude: fails one or more inclusion criteria, or triggers an exclusion criterion\n\n"
    "Do not include articles where `fulltext` is null (no PDF available) in your decisions.\n\n"
    "Respond with JSON only:\n"
    '{"decisions": [{"orig_idx": N, "decision": "include|exclude", '
    '"rationale": "brief reason citing specific criterion", '
    '"study_design": "e.g. RCT, cohort study, systematic review"}]}'
)


def _read_pdf_full_text(pdf_path: Path) -> str | None:
    """Concatenated text of ALL pages (uncapped), or None if unreadable/empty."""
    if not _PYPDF_AVAILABLE:
        return None
    try:
        reader = pypdf.PdfReader(str(pdf_path))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
        return text if text.strip() else None
    except Exception:
        return None


def _head_tail_excerpt(full_text: str) -> str:
    """First FULLTEXT_HEAD_CHARS + last FULLTEXT_TAIL_CHARS chars, joined by a truncation marker.

    Returns the whole text unchanged when it already fits under the cap (no marker, no duplicated
    tail). The head/tail never overlap because HEAD+TAIL == FULLTEXT_MAX_CHARS < len(full_text).
    """
    if len(full_text) <= FULLTEXT_MAX_CHARS:
        return full_text
    head = full_text[:FULLTEXT_HEAD_CHARS]
    tail = full_text[-FULLTEXT_TAIL_CHARS:]
    return f"{head}\n\n[... middle of article truncated ...]\n\n{tail}"


def _extract_text_and_full(pdf_path: Path) -> tuple[str | None, str | None]:
    """Single pypdf pass → (head+tail excerpt for the LLM payload, uncapped full text).

    The excerpt bounds prompt size (Stages 10/11/13); the full text feeds the title-coverage
    match-confidence check (#62), which must see the whole document, not just the first pages.
    """
    full_text = _read_pdf_full_text(pdf_path)
    if full_text is None:
        return None, None
    return _head_tail_excerpt(full_text), full_text


def _extract_text(pdf_path: Path) -> str | None:
    """Head+tail excerpt (capped at FULLTEXT_MAX_CHARS) for backfill/persistence callers."""
    return _extract_text_and_full(pdf_path)[0]


def _match_pdfs(articles: list[Article], pdf_dir: Path) -> dict[int, Path]:
    """Return {article_index: pdf_path} for articles matched to PDFs by PMID or DOI."""
    if not pdf_dir.exists():
        return {}
    # iter_pdfs matches the .pdf extension case-insensitively (an uppercase `.PDF` would be
    # skipped by glob('*.pdf') and the article silently assessed as PDF-less).
    pdf_files = {p.stem.lower(): p for p in iter_pdfs(pdf_dir)}
    matched: dict[int, Path] = {}
    for i, article in enumerate(articles):
        if article.pmid and article.pmid.lower() in pdf_files:
            matched[i] = pdf_files[article.pmid.lower()]
        elif article.doi:
            doi_stem = _normalize_doi_for_filename(article.doi)
            if doi_stem in pdf_files:
                matched[i] = pdf_files[doi_stem]
    return matched


def _persist_fulltext_decisions_to_screened(
    review_dir: Path, review, eligible: list[tuple[int, Article]]
) -> None:
    """Write terminal (include/exclude) fulltext_decision values back to articles_screened.json.

    Used by the iteration_only save so a later iteration round scopes out already-assessed
    candidates. No-PDF pending candidates are intentionally left unset so a future round (once
    their PDF arrives) can re-attempt them.
    """
    screened_path = review_dir / review.articles_screened_file
    rows = json.loads(screened_path.read_text())
    terminal = {
        orig_idx: art.fulltext_decision.value
        for orig_idx, art in eligible
        if art.fulltext_decision in (ScreeningDecision.include, ScreeningDecision.exclude)
    }
    for i, row in enumerate(rows):
        if i in terminal:
            row["fulltext_decision"] = terminal[i]
    screened_path.write_text(json.dumps(rows, indent=2))


def _load_eligible(review_dir: Path, review, iteration_only: bool = False) -> list[tuple[int, Article]]:
    """Return list of (orig_idx, article) for include/borderline-screened articles.

    orig_idx is always the position in articles_screened.json (so decisions map + slicing align).
    When iteration_only is set, restrict to Checkpoint-5 iteration candidates (iteration_added=True)
    that have NOT yet reached a terminal full-text decision — i.e. fulltext_decision is unset/pending.
    save(iteration_only) persists each terminal include/exclude back to articles_screened.json, so a
    later iteration round never re-assesses (or double-counts) an already-assessed candidate, and the
    already-approved primary/CC includes (not iteration_added) are never touched.
    """
    articles_raw = json.loads((review_dir / review.articles_screened_file).read_text())
    pairs = [
        (i, Article(**a))
        for i, a in enumerate(articles_raw)
        if a.get("screening_decision") in ("include", "borderline")
    ]
    if not iteration_only:
        return pairs
    return [
        (i, art) for i, art in pairs
        if art.iteration_added and art.fulltext_decision in (None, ScreeningDecision.pending)
    ]


def _persist_excerpts_to_screened(
    review_dir: Path, review, eligible: list[tuple[int, Article]]
) -> None:
    """Write fulltext_excerpt values from `eligible` back to articles_screened.json in place."""
    screened_path = review_dir / review.articles_screened_file
    rows = json.loads(screened_path.read_text())
    excerpt_map = {orig_idx: a.fulltext_excerpt for orig_idx, a in eligible}
    for i, row in enumerate(rows):
        if i in excerpt_map:
            row["fulltext_excerpt"] = excerpt_map[i]
    screened_path.write_text(json.dumps(rows, indent=2))


def _attach_excerpts(
    articles: list[Article], pdf_dir: Path, cache: dict[Path, str | None]
) -> int:
    """Match PDFs to articles, populate fulltext_excerpt, return fill count.

    `cache` is keyed by resolved PDF path so a PDF that backs an article in two
    parallel sets (e.g., included + screened) is parsed exactly once per backfill.
    """
    pdf_matches = _match_pdfs(articles, pdf_dir)
    filled = 0
    for local_idx, article in enumerate(articles):
        pdf_path = pdf_matches.get(local_idx)
        if pdf_path is None:
            continue
        if pdf_path not in cache:
            cache[pdf_path] = _extract_text(pdf_path)
        excerpt = cache[pdf_path]
        if excerpt:
            article.fulltext_excerpt = excerpt
            filled += 1
    return filled


def persist_pdf_text(review_dir: Path, pdf_dir: Path | None = None) -> None:
    """Backfill fulltext_excerpt onto an already-completed review.

    Re-runs Stage 10's PDF match + extraction against articles_included.json (and
    articles_screened.json for include/borderline rows) without invoking any LLM.
    Use after the fulltext-persistence fix on reviews whose Stage 10 ran beforehand.
    """
    review = state_manager.load(review_dir)
    if pdf_dir is None:
        pdf_dir = review_dir / "pdfs"

    # Cache shared across both backfill passes so each PDF is parsed once.
    excerpt_cache: dict[Path, str | None] = {}

    included_path = review_dir / review.articles_included_file
    included_articles: list[Article] = []
    included_filled = 0
    if included_path.exists():
        included_articles = [Article(**a) for a in json.loads(included_path.read_text())]
        included_filled = _attach_excerpts(included_articles, pdf_dir, excerpt_cache)
        included_path.write_text(
            json.dumps([a.model_dump(mode="json") for a in included_articles], indent=2)
        )

    eligible = _load_eligible(review_dir, review)
    screened_filled = _attach_excerpts([a for _, a in eligible], pdf_dir, excerpt_cache)
    _persist_excerpts_to_screened(review_dir, review, eligible)

    print(prepare_output({
        "included_articles": len(included_articles),
        "included_excerpts_filled": included_filled,
        "eligible_screened": len(eligible),
        "screened_excerpts_filled": screened_filled,
    }))


_OVERLAP_STOPWORDS = {
    "the", "and", "for", "with", "from", "into", "that", "this", "these",
    "those", "have", "has", "are", "was", "were", "been", "being", "their",
    "they", "them", "its", "his", "her", "our", "your", "study", "studies",
    "patient", "patients", "using", "use", "used", "results", "outcome",
    "outcomes",
}


def _content_words(text: str) -> set[str]:
    """Lowercase tokens of length >=4 that aren't common stopwords."""
    tokens = re.findall(r"[A-Za-z]{4,}", text.lower())
    return {t for t in tokens if t not in _OVERLAP_STOPWORDS}


def _title_coverage_in_pdf(title: str, full_text: str) -> float:
    """Fraction of the title's significant words that appear ANYWHERE in the full PDF text.

    Coverage (not Jaccard) because short titles have few content words: a 3–6-word title diluted
    into a page of author/affiliation metadata scores near-zero on Jaccard even when every title
    word is present. The denominator is title-word count, so full presence == 1.0 regardless of
    document length, and a genuinely off-topic PDF still scores near-zero (#48 protection).
    """
    title_words = _content_words(title)
    if not title_words:
        return 0.0
    text_words = _content_words(full_text)
    return len(title_words & text_words) / len(title_words)


_PDF_MIN_LENGTH = 500
_PDF_MIN_COVERAGE = 0.50


def _pdf_match_confidence(title: str, full_text: str | None) -> tuple[bool, str]:
    """Return (low_confidence, reason) from the UNCAPPED full PDF text. low_confidence=True means
    the PDF likely doesn't match the article; reason is a short string for the warnings array.
    """
    if not full_text:
        return False, ""  # absent text is handled elsewhere as pdf_found=False
    if len(full_text) < _PDF_MIN_LENGTH:
        return True, f"extracted text length {len(full_text)} < {_PDF_MIN_LENGTH} chars"
    coverage = _title_coverage_in_pdf(title, full_text)
    if coverage < _PDF_MIN_COVERAGE:
        return True, f"title-word coverage {coverage:.2f} < {_PDF_MIN_COVERAGE}"
    return False, ""


def prepare(
    review_dir: Path,
    pdf_dir: Path | None = None,
    slice_of: int | None = None,
    slice_index: int | None = None,
    iteration_only: bool = False,
) -> None:
    validate_slice_spec(slice_of, slice_index)
    review = state_manager.load(review_dir)
    if review.status not in (ReviewStatus.retrieval_list_generated, ReviewStatus.pdfs_downloaded):
        die(f"Expected status 'retrieval_list_generated' or 'pdfs_downloaded', got '{review.status}'.")
    if review.pico is None:
        die("PICO not set.")

    # Issue #47: reconcile disk state with retrieval_list.csv so files dropped
    # in pdfs/ outside the pipeline (manual library fetch, Stage 9.6 import)
    # become visible to the eligible-set computation. Skip in slice mode —
    # the Stage 10 prompt always runs a non-sliced probe first which has
    # already reconciled the CSV; running again from each parallel slice
    # would race on retrieval_list.csv read-modify-write.
    if slice_of is None:
        from src.skills.pdf_downloader import reconcile_disk
        reconcile_summary = reconcile_disk(review_dir)
    else:
        reconcile_summary = {"reconciled": 0, "already_correct": 0, "no_match_in_csv": []}

    if pdf_dir is None:
        pdf_dir = review_dir / "pdfs"

    eligible = _load_eligible(review_dir, review, iteration_only=iteration_only)
    # Sort by orig_idx for deterministic slicing across parallel sub-agents.
    eligible.sort(key=lambda pair: pair[0])
    total_eligible = len(eligible)
    if slice_of is not None:
        start, end = slice_bounds(total_eligible, slice_of, slice_index)
        eligible = eligible[start:end]
    pdf_matches = _match_pdfs([a for _, a in eligible], pdf_dir)

    articles_out = []
    pdf_match_warnings: list[dict] = []
    for local_idx, (orig_idx, article) in enumerate(eligible):
        pdf_path = pdf_matches.get(local_idx)
        # One pypdf pass yields the capped excerpt (for the payload / Stages 11/13) and the
        # uncapped full text (for the coverage match-confidence check).
        fulltext, full_text = _extract_text_and_full(pdf_path) if pdf_path is not None else (None, None)
        # Persist on the in-memory record so save() (and any backfill of articles_screened.json
        # below) carries the excerpt forward to Stages 11/13 without re-parsing the PDF.
        article.fulltext_excerpt = fulltext

        low_conf, reason = _pdf_match_confidence(article.title or "", full_text)
        if low_conf:
            pdf_match_warnings.append({
                "orig_idx": orig_idx,
                "title": article.title,
                "reason": reason,
            })

        articles_out.append({
            "orig_idx": orig_idx,
            "title": article.title,
            "authors": article.authors[:3],
            "year": article.year,
            "screening_decision": article.screening_decision.value,
            "pdf_found": pdf_path is not None,
            "pdf_low_confidence": low_conf,
            "fulltext": fulltext,
        })

    # Write extracted excerpts back to articles_screened.json so they survive across CLI invocations.
    # save() reloads the file fresh, so without this writeback the excerpts would be lost.
    # Skip in slice mode (same reasoning as the reconcile_disk guard above): each parallel slice
    # would do a full read-modify-write of the shared file, and disjoint writers lose each other's
    # updates (and can tear the file). The non-sliced Step-1 probe persists all excerpts first.
    if slice_of is None:
        _persist_excerpts_to_screened(review_dir, review, eligible)

    pico = review.pico
    output: dict = {
        "review_id": review.review_id,
        "total_eligible": total_eligible,
        "pdfs_matched": len(pdf_matches),
        "pico_context": _build_pico_context(review),
        "pico": {"P": pico.P, "I": pico.I, "C": pico.C, "O": pico.O, "S": pico.S},
        "inclusion_criteria": pico.inclusion_criteria,
        "exclusion_criteria": pico.exclusion_criteria,
        "articles": articles_out,
        "assessment_task": ASSESSMENT_TASK,
        "pdf_match_warnings": pdf_match_warnings,
        "low_confidence_count": len(pdf_match_warnings),
        "reconcile_summary": reconcile_summary,
    }
    if slice_of is not None:
        output["slice_of"] = slice_of
        output["slice_index"] = slice_index
    print(prepare_output(output))


def _apply_decisions(
    eligible: list[tuple[int, Article]], decision_map: dict[int, dict]
) -> tuple[list[Article], int, dict[str, int]]:
    """Apply fulltext decisions to eligible articles. Returns (included, assessed, excluded_reasons)."""
    included: list[Article] = []
    assessed = 0
    excluded_reasons: dict[str, int] = {}

    for orig_idx, article in eligible:
        if orig_idx not in decision_map:
            article.fulltext_decision = ScreeningDecision.pending
            article.fulltext_rationale = "PDF not provided; pending retrieval"
            continue
        d = decision_map[orig_idx]
        decision_str = (d.get("decision") or "exclude").lower()
        article.fulltext_decision = (
            ScreeningDecision.include if decision_str == "include" else ScreeningDecision.exclude
        )
        article.fulltext_rationale = d.get("rationale", "")
        if d.get("study_design"):
            assign_study_design(article, d["study_design"])
        assessed += 1
        if article.fulltext_decision == ScreeningDecision.include:
            included.append(article)
        else:
            reason = (article.fulltext_rationale or "unspecified")[:80]
            excluded_reasons[reason] = excluded_reasons.get(reason, 0) + 1

    return included, assessed, excluded_reasons


def save(review_dir: Path, input_json: str, iteration_only: bool = False) -> None:
    data = parse_save_input(input_json)
    decisions = data.get("decisions", [])

    review = state_manager.load(review_dir)
    eligible = _load_eligible(review_dir, review, iteration_only=iteration_only)
    decision_map: dict[int, dict] = {
        d["orig_idx"]: d for d in decisions if isinstance(d.get("orig_idx"), int)
    }

    included, assessed, excluded_reasons = _apply_decisions(eligible, decision_map)
    included_path = review_dir / review.articles_included_file
    p = review.prisma_counts

    if iteration_only:
        # Scoped Checkpoint-5 re-run: APPEND survivors to the already-approved included set and
        # INCREMENT the PRISMA counts — never rebuild (which would drop the primary/CC includes
        # that carry no decision in this call). eligibility/assessed/not_retrieved accumulate the
        # iteration delta so the streams stay honest.
        existing = [Article(**a) for a in json.loads(included_path.read_text())] if included_path.exists() else []
        combined = existing + included
        included_path.write_text(json.dumps([a.model_dump(mode="json") for a in combined], indent=2))
        # Record terminal decisions on the screened file so a later iteration round scopes these out.
        _persist_fulltext_decisions_to_screened(review_dir, review, eligible)
        p.eligibility += len(eligible)
        p.fulltext_assessed += assessed
        p.not_retrieved += len(eligible) - assessed
        p.included = len(combined)
        for reason, count in excluded_reasons.items():
            p.excluded_fulltext[reason] = p.excluded_fulltext.get(reason, 0) + count
        included_count = len(combined)
    else:
        included_path.write_text(json.dumps([a.model_dump(mode="json") for a in included], indent=2))
        # eligibility = reports SOUGHT for full-text (include + borderline). Do NOT overwrite it
        # with `assessed` — that drops the no-PDF "pending" articles out of the PRISMA chain.
        # Track assessed vs not-retrieved separately (sought = assessed + not_retrieved;
        # assessed = included + excluded_fulltext).
        p.eligibility = len(eligible)
        p.fulltext_assessed = assessed
        p.not_retrieved = len(eligible) - assessed
        p.included = len(included)
        p.excluded_fulltext = excluded_reasons
        included_count = len(included)

    review.status = ReviewStatus.fulltext_assessed
    state_manager.save(review, review_dir)

    print(prepare_output({
        "eligible_for_fulltext": len(eligible),
        "assessed": assessed,
        "included": included_count,
        "excluded_fulltext": assessed - len(included),
        "pending_retrieval": len(eligible) - assessed,
    }))



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Full-text assessor — subscription mode")
    parser.add_argument("--review-dir", required=True, metavar="DIR")
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument("--prepare", action="store_true")
    mode_group.add_argument("--save", action="store_true")
    mode_group.add_argument("--persist-pdf-text", action="store_true",
                            help="Backfill fulltext_excerpt onto already-completed reviews; no LLM call.")
    parser.add_argument("--pdf-dir", metavar="DIR", help="PDF directory (default: review-dir/pdfs/)")
    parser.add_argument("--slice-of", type=int, default=None, metavar="N",
                        help="Total number of slices for parallel sub-agent fan-out (Issue #45)")
    parser.add_argument("--slice-index", type=int, default=None, metavar="I",
                        help="Slice index (0..N-1) when --slice-of is set")
    parser.add_argument("--iteration-only", action="store_true",
                        help="Scoped Checkpoint-5 re-run: assess only new iteration includes and "
                             "APPEND survivors to articles_included.json (never re-assess the primary set).")
    parser.add_argument("--input-json", default=None, metavar="JSON")
    args = parser.parse_args()

    review_dir = Path(args.review_dir)
    pdf_dir = Path(args.pdf_dir) if args.pdf_dir else None

    if args.prepare:
        prepare(
            review_dir=review_dir,
            pdf_dir=pdf_dir,
            slice_of=args.slice_of,
            slice_index=args.slice_index,
            iteration_only=args.iteration_only,
        )
    elif args.persist_pdf_text:
        persist_pdf_text(review_dir, pdf_dir)
    else:
        if args.input_json is None:
            die("--input-json is required with --save.")
        save(review_dir, args.input_json, iteration_only=args.iteration_only)
