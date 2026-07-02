"""Citation chasing — backward (references) and forward (citing papers). Stage 8.5.

Runs after abstract screening. Seeds from abstract-screened includes only (not borderlines).
New candidates are presented for screening within this same stage, then citation-chased includes
are appended to articles_screened.json so Stage 9 (retrieval list) picks them up in a single pass.

Backward chasing:
  CrossRef get_references per included article → extract DOIs → resolve metadata

Forward chasing:
  Semantic Scholar get_citations per article with s2_id → batch_lookup

New candidates are deduplicated against all previously screened DOIs/titles.
PRISMA citation_chasing_identified/screened/added counters are updated.

Input:  review_dir (reads articles_screened.json filtered to include only;
        status must be abstract_screening)
Output: articles_screened.json updated with citation-chased includes;
        search_log.md appended; status=citation_chased
CLI:
  # Build candidates (network pipeline; writes _citation_candidates.json):
  python -m src.skills.citation_chaser --review-dir reviews/{slug} --prepare
  # Parallel mode: bare --prepare MUST run first; subsequent slice calls reuse the file.
  python -m src.skills.citation_chaser --review-dir reviews/{slug} --prepare \\
      --slice-of N --slice-index I
  python -m src.skills.citation_chaser --review-dir reviews/{slug} --save \\
      --input-json '{"decisions": [...]}'
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.agent import state as state_manager
from src.integrations.config import load_api_config
from src.integrations.crossref_client import CrossRefClient
from src.integrations.openalex_client import OpenAlexClient
from src.integrations.pubmed_client import PubMedClient
from src.integrations.semantic_scholar_client import SemanticScholarClient
from src.models.article import Article, ScreeningDecision
from src.models.review import ReviewStatus
from src.skills._base import die, parse_save_input, prepare_output, slice_bounds, validate_slice_spec
from src.skills._keywords import _STOP_WORDS, _SYNONYM_FAMILIES, _extract_terms
from src.skills.abstract_screener import _build_pico_context
from src.skills.deduplicator import _normalize_doi

ABSTRACT_MAX_CHARS = 1500
_CANDIDATES_FILE = "_citation_candidates.json"

_SCREENING_TASK = (
    "These articles were found through citation chasing (backward reference lists + forward citing papers). "
    "Screen each against the PICO criteria and inclusion/exclusion criteria in `pico_context`. "
    "For each article decide: include, exclude, or borderline.\n\n"
    "Respond with ONLY valid JSON:\n"
    '{"decisions": [{"index": N, "decision": "include|exclude|borderline", "rationale": "1 sentence"}]}'
)


def _keyword_prefilter(
    candidates: list[Article],
    pico_p: str,
    pico_i: str,
) -> tuple[list[Article], int, dict]:
    """Pre-filter using P AND I keyword requirement. Returns (kept, dropped_count, info).

    Uses synonym families categorized as 'population' or 'intervention'. PICO P text
    triggers population families; PICO I text triggers intervention families. Candidates
    must match ≥1 p_term AND ≥1 i_term. If no family is triggered for either category,
    pre-filtering is skipped for that axis (returns all candidates).
    """
    raw_p = _extract_terms(pico_p)
    raw_i = _extract_terms(pico_i)

    p_terms: set[str] = set()
    i_terms: set[str] = set()
    for category, triggers, synonyms in _SYNONYM_FAMILIES:
        if category == "population" and raw_p & triggers:
            p_terms |= synonyms
        elif category == "intervention" and raw_i & triggers:
            i_terms |= synonyms

    if not p_terms or not i_terms:
        return candidates, 0, {"skipped": True, "reason": "no_synonym_families_triggered",
                               "p_terms": sorted(p_terms), "i_terms": sorted(i_terms)}

    kept, dropped = [], 0
    for candidate in candidates:
        text = f"{candidate.title or ''} {candidate.abstract or ''}".lower()
        if any(t in text for t in p_terms) and any(t in text for t in i_terms):
            kept.append(candidate)
        else:
            dropped += 1

    return kept, dropped, {
        "operator": "AND",
        "p_terms": sorted(p_terms),
        "i_terms": sorted(i_terms),
    }


def _build_known_dois(articles: list[Article]) -> set[str]:
    return {_normalize_doi(a.doi) for a in articles if a.doi}


def _build_known_titles(articles: list[Article]) -> set[str]:
    return {a.title.lower().strip() for a in articles if a.title}


def _is_duplicate(
    candidate: Article,
    known_dois: set[str],
    known_titles: set[str],
) -> bool:
    if candidate.doi and _normalize_doi(candidate.doi) in known_dois:
        return True
    if candidate.title and candidate.title.lower().strip() in known_titles:
        return True
    return False


async def _backward_chase(
    crossref: CrossRefClient,
    included: list[Article],
    known_dois: set[str],
) -> list[Article]:
    doi_articles = [a for a in included if a.doi]
    if not doi_articles:
        return []

    async def _get_refs(article: Article) -> list[str]:
        try:
            return await crossref.get_references(article.doi)
        except Exception:
            return []

    ref_lists = await asyncio.gather(*[_get_refs(a) for a in doi_articles])
    ref_dois: set[str] = set()
    for refs in ref_lists:
        ref_dois.update(_normalize_doi(r) for r in refs if r)

    new_dois = ref_dois - known_dois - {None}

    async def _resolve(doi: str) -> Article | None:
        try:
            return await crossref.resolve_doi(doi)
        except Exception:
            return None

    resolved = await asyncio.gather(*[_resolve(doi) for doi in new_dois])
    return [a for a in resolved if a and a.title]


async def _enrich_abstracts(s2: SemanticScholarClient, candidates: list[Article]) -> None:
    keys = [f"DOI:{a.doi}" for a in candidates if a.doi and not a.abstract]
    if not keys:
        return
    try:
        enriched = await s2.batch_lookup(keys)
    except Exception:
        return  # S2 unreachable; leave candidates as-is (abstract_unavailable not set)
    doi_to_abstract: dict[str, str] = {
        e.doi.lower(): e.abstract for e in enriched if e.doi and e.abstract
    }
    for candidate in candidates:
        if candidate.doi and not candidate.abstract:
            abstract = doi_to_abstract.get(candidate.doi.lower())
            if abstract:
                candidate.abstract = abstract
            else:
                candidate.abstract_unavailable = True


async def _enrich_seeds_s2(s2: SemanticScholarClient, seeds: list[Article]) -> None:
    keys = [
        f"DOI:{a.doi}" if a.doi else f"PMID:{a.pmid}"
        for a in seeds
        if a.s2_id is None and (a.doi or a.pmid)
    ]
    if not keys:
        return
    try:
        enriched = await s2.batch_lookup(keys)
    except Exception:
        return
    doi_to_s2: dict[str, str] = {
        e.doi.lower(): e.s2_id for e in enriched if e.doi and e.s2_id
    }
    pmid_to_s2: dict[str, str] = {
        e.pmid: e.s2_id for e in enriched if e.pmid and e.s2_id
    }
    for seed in seeds:
        if seed.s2_id is None:
            if seed.doi and seed.doi.lower() in doi_to_s2:
                seed.s2_id = doi_to_s2[seed.doi.lower()]
            elif seed.pmid and seed.pmid in pmid_to_s2:
                seed.s2_id = pmid_to_s2[seed.pmid]


async def _forward_chase(
    s2: SemanticScholarClient,
    included: list[Article],
    known_dois: set[str],
) -> list[Article]:
    async def _get_citing(article: Article) -> set[str]:
        if not article.s2_id:
            return set()
        try:
            return set(await s2.get_citations(article.s2_id))
        except Exception:
            return set()

    results = await asyncio.gather(*[_get_citing(a) for a in included])
    citing_ids = set().union(*results)

    if not citing_ids:
        return []

    enriched = await s2.batch_lookup(list(citing_ids))
    return [
        a for a in enriched
        if a.title and not (a.doi and _normalize_doi(a.doi) in known_dois)
    ]


def _append_search_log(review_dir: Path, log_entry: str) -> None:
    with open(review_dir / "search_log.md", "a", encoding="utf-8") as f:
        f.write("\n" + log_entry)


async def _enrich_missing_identifiers(
    articles: list[Article],
    crossref: CrossRefClient,
    s2: SemanticScholarClient,
    pubmed: PubMedClient,
) -> dict:
    """For each article with no doi AND no pmid, attempt CrossRef → S2 → PubMed recovery.

    Mutates articles in-place. Returns enrichment stats dict. Failures are non-blocking —
    articles with no recoverable identifier remain valid candidates.
    """
    recovered_crossref = recovered_s2 = recovered_pubmed = still_missing = 0
    targets = [a for a in articles if not a.doi and not a.pmid]

    for article in targets:
        if not article.title:
            still_missing += 1
            continue

        first_author_family = ""
        if article.authors:
            raw = article.authors[0] or ""
            first_author_family = raw.split()[-1] if raw.strip() else ""

        # Step 1: CrossRef title search (returns DOI if confidence score > 80)
        try:
            doi = await crossref.search_by_title(article.title, first_author_family, article.year)
        except Exception:
            doi = None
        if doi:
            article.doi = doi
            recovered_crossref += 1
            continue

        # Step 2: S2 title search (covers articles not in CrossRef)
        try:
            s2_result = await s2.search_paper(article.title, article.year)
        except Exception:
            s2_result = None
        if s2_result:
            article.doi = s2_result.doi
            article.pmid = s2_result.pmid
            if not article.s2_id:
                article.s2_id = s2_result.s2_id
            if article.doi or article.pmid:
                recovered_s2 += 1
                continue

        # Step 3: PubMed exact title search (only if Steps 1–2 failed)
        try:
            pmid = await pubmed.search_by_title(article.title)
        except Exception:
            pmid = None
        if pmid:
            article.pmid = pmid
            recovered_pubmed += 1
            continue

        still_missing += 1

    return {
        "candidates_missing_ids": len(targets),
        "recovered_by_crossref": recovered_crossref,
        "recovered_by_s2": recovered_s2,
        "recovered_by_pubmed": recovered_pubmed,
        "still_missing": still_missing,
    }


def _is_metadata_complete(article: Article) -> bool:
    """An article is metadata-complete when authors, journal, year, and abstract
    are all populated. volume/issue/pages are nice-to-have but not required."""
    return bool(article.authors and article.journal and article.year and article.abstract)


def _apply_enrichment(target: Article, source: Article) -> bool:
    """Copy any non-null fields from source into target where target is empty.
    Returns True if at least one field was filled."""
    changed = False
    for field in ("authors", "journal", "year", "volume", "issue", "pages", "abstract"):
        if not getattr(target, field, None):
            value = getattr(source, field, None)
            if value:
                setattr(target, field, value)
                changed = True
    return changed


def _zero_metadata_stats() -> dict:
    """Zero-filled stats dict with the same shape as _enrich_missing_metadata's return."""
    return {
        "total": 0,
        "no_doi": 0,
        "already_complete": 0,
        "enriched_by_crossref": 0,
        "enriched_by_openalex": 0,
        "enriched_by_s2": 0,
        "crossref_errors": 0,
        "openalex_errors": 0,
        "s2_errors": 0,
        "still_incomplete": 0,
    }


async def _enrich_one_article_metadata(
    article: Article, crossref, openalex, stats: dict
) -> None:
    """Per-article CrossRef → OpenAlex waterfall used by _enrich_missing_metadata.
    S2 is handled as a single batch outside this helper."""
    if _is_metadata_complete(article):
        stats["already_complete"] += 1
        return
    if not article.doi:
        stats["no_doi"] += 1
        return

    try:
        crossref_result = await crossref.resolve_doi(article.doi)
        if crossref_result and _apply_enrichment(article, crossref_result):
            stats["enriched_by_crossref"] += 1
    except Exception:
        stats["crossref_errors"] += 1

    if _is_metadata_complete(article):
        return

    try:
        openalex_result = await openalex.get_work(article.doi)
        if openalex_result and _apply_enrichment(article, openalex_result):
            stats["enriched_by_openalex"] += 1
    except Exception:
        stats["openalex_errors"] += 1


async def _enrich_missing_metadata(
    articles: list[Article],
    crossref,
    openalex,
    s2,
) -> dict:
    """Issue #49: fill missing authors/journal/year/volume/issue/pages/abstract
    for citation-chased articles. Per-article CrossRef → OpenAlex runs in
    parallel via asyncio.gather (matches _backward_chase/_forward_chase
    pattern); S2 mops up stragglers in a single batch_lookup (matches
    _enrich_abstracts pattern).

    Failures (network, rate limit) are non-blocking; the article keeps whatever
    it already had and the per-source error counter is incremented."""
    stats = _zero_metadata_stats()
    stats["total"] = len(articles)

    await asyncio.gather(*(
        _enrich_one_article_metadata(a, crossref, openalex, stats) for a in articles
    ))

    s2_targets = [a for a in articles if a.doi and not _is_metadata_complete(a)]
    if s2_targets:
        keys = [f"DOI:{a.doi}" for a in s2_targets]
        try:
            s2_results = await s2.batch_lookup(keys)
            by_doi = {
                r.doi.lower(): r for r in s2_results if r and r.doi
            }
            for article in s2_targets:
                result = by_doi.get(article.doi.lower())
                if result and _apply_enrichment(article, result):
                    stats["enriched_by_s2"] += 1
        except Exception:
            stats["s2_errors"] += len(s2_targets)

    stats["still_incomplete"] = sum(
        1 for a in articles if a.doi and not _is_metadata_complete(a)
    )
    return stats


def _emit_output(
    review,
    candidates_data: dict,
    slice_of: int | None,
    slice_index: int | None,
    retrieval_errors: list[str],
) -> None:
    candidates = [Article(**a) for a in candidates_data.get("candidates", [])]
    total = len(candidates)

    if slice_of is not None:
        start, end = slice_bounds(total, slice_of, slice_index)
        slice_indices = list(range(start, end))
        sliced = candidates[start:end]
    else:
        slice_indices = list(range(total))
        sliced = candidates

    output = {
        "backward_count": candidates_data.get("backward_count", 0),
        "forward_count": candidates_data.get("forward_count", 0),
        "pre_filter_total": candidates_data.get("pre_filter_total", 0),
        "pre_filtered_count": candidates_data.get("pre_filtered_count", 0),
        "total_candidates": total,
        "filter_info": candidates_data.get("filter_info", {}),
        "enrichment": candidates_data.get("enrichment", {}),
        "candidates": [
            {
                "index": idx,
                "title": c.title,
                "abstract": (c.abstract or "No abstract available")[:ABSTRACT_MAX_CHARS],
                "year": c.year,
            }
            for idx, c in zip(slice_indices, sliced)
        ],
        "pico_context": _build_pico_context(review),
        "screening_task": _SCREENING_TASK,
        "retrieval_errors": retrieval_errors,
    }
    if slice_of is not None:
        output["slice_of"] = slice_of
        output["slice_index"] = slice_index
        output["slice_pending"] = len(sliced)
        output["slice_indices"] = slice_indices
    print(prepare_output(output))


async def _prepare_async(review_dir: Path) -> None:
    review = state_manager.load(review_dir)
    if review.status != ReviewStatus.abstract_screening:
        die(f"Expected status 'abstract_screening', got '{review.status}'.")
    if review.pico is None:
        die("PICO not set.")

    screened_path = review_dir / review.articles_screened_file
    all_screened = (
        [Article(**a) for a in json.loads(screened_path.read_text())]
        if screened_path.exists()
        else []
    )

    # Seed from abstract-screened includes and borderlines
    seed_articles = [
        a for a in all_screened
        if a.screening_decision in (ScreeningDecision.include, ScreeningDecision.borderline)
    ]

    # Dedup against all screened articles
    known_dois = _build_known_dois(all_screened)
    known_titles = _build_known_titles(all_screened)

    cfg = load_api_config()
    crossref = CrossRefClient(mailto=cfg.crossref_mailto)
    s2 = SemanticScholarClient(api_key=cfg.semantic_scholar_api_key)
    pubmed = PubMedClient(api_key=cfg.ncbi_api_key)

    retrieval_errors: list[str] = []
    if not cfg.semantic_scholar_api_key:
        retrieval_errors.append(
            "semantic_scholar: API key not configured — forward chasing will hit free-tier "
            "rate limits. Get one free at semanticscholar.org/product/api and set "
            "semantic_scholar_api_key in config/api_keys.yaml"
        )

    (_, backward_raw) = await asyncio.gather(
        _enrich_seeds_s2(s2, seed_articles),
        _backward_chase(crossref, seed_articles, known_dois),
    )
    forward_raw = await _forward_chase(s2, seed_articles, known_dois)

    await _enrich_abstracts(s2, backward_raw)

    all_candidates: list[Article] = []
    seen_in_candidates: set[str] = set()
    for candidate in backward_raw + forward_raw:
        key = _normalize_doi(candidate.doi) or candidate.title.lower().strip()
        if key and key not in seen_in_candidates and not _is_duplicate(candidate, known_dois, known_titles):
            all_candidates.append(candidate)
            seen_in_candidates.add(key)

    # Keyword pre-filter: require P AND I terms in title+abstract before LLM screening.
    pre_filter_total = len(all_candidates)
    if review.pico:
        all_candidates, pre_filtered_count, filter_info = _keyword_prefilter(
            all_candidates, review.pico.P, review.pico.I
        )
    else:
        pre_filtered_count, filter_info = 0, {"skipped": True, "reason": "no_pico"}

    # Identifier enrichment: recover doi/pmid for candidates that have neither.
    enrichment_stats = await _enrich_missing_identifiers(all_candidates, crossref, s2, pubmed)

    candidates_data = {
        "backward_count": len(backward_raw),
        "forward_count": len(forward_raw),
        "pre_filter_total": pre_filter_total,
        "pre_filtered_count": pre_filtered_count,
        "filter_info": filter_info,
        "enrichment": enrichment_stats,
        "candidates": [a.model_dump(mode="json") for a in all_candidates],
    }
    (review_dir / _CANDIDATES_FILE).write_text(
        json.dumps(candidates_data, indent=2),
        encoding="utf-8",
    )

    _emit_output(review, candidates_data, slice_of=None, slice_index=None, retrieval_errors=retrieval_errors)


def prepare(
    review_dir: Path,
    slice_of: int | None = None,
    slice_index: int | None = None,
) -> None:
    validate_slice_spec(slice_of, slice_index)
    candidates_path = review_dir / _CANDIDATES_FILE
    if candidates_path.exists():
        review = state_manager.load(review_dir)
        if review.status != ReviewStatus.abstract_screening:
            die(f"Expected status 'abstract_screening', got '{review.status}'.")
        candidates_data = json.loads(candidates_path.read_text())
        _emit_output(review, candidates_data, slice_of, slice_index, retrieval_errors=[])
        return
    if slice_of is not None:
        die(
            f"{_CANDIDATES_FILE} not found. Run --prepare without --slice-of first to "
            "build the candidate set; slice prepares reuse that file."
        )
    asyncio.run(_prepare_async(review_dir))


def save(review_dir: Path, input_json: str) -> None:
    candidates_path = review_dir / _CANDIDATES_FILE
    if not candidates_path.exists():
        die(f"{_CANDIDATES_FILE} not found. Run --prepare first.")

    try:
        data = parse_save_input(input_json)
    except ValueError as exc:
        die(str(exc))

    candidates_data = json.loads(candidates_path.read_text())
    backward_count = candidates_data.get("backward_count", 0)
    forward_count = candidates_data.get("forward_count", 0)
    pre_filter_total = candidates_data.get("pre_filter_total", backward_count + forward_count)
    pre_filtered_count = candidates_data.get("pre_filtered_count", 0)
    filter_info = candidates_data.get("filter_info", {})
    all_candidates = [Article(**a) for a in candidates_data.get("candidates", [])]
    # Defensive parse (mirrors abstract_screener.save): skip malformed entries
    # rather than crashing — a missing "index", or a missing/null "decision",
    # would otherwise raise KeyError/AttributeError uncaught, stranding the review
    # at abstract_screening and forcing the sunk network chase to be re-screened.
    decisions: dict[int, str] = {}
    for d in data.get("decisions", []):
        idx = d.get("index")
        decision = d.get("decision")
        if not isinstance(idx, int) or not isinstance(decision, str):
            continue
        decisions[idx] = decision.lower()

    new_includes: list[Article] = []
    new_borderlines: list[Article] = []
    for i, candidate in enumerate(all_candidates):
        candidate.citation_chased = True
        # A candidate absent from the merged decisions map defaults to borderline (Issue #31):
        # a citation-chased candidate that was not explicitly excluded still gets a full-text
        # look (and a human decision at Checkpoint 5) rather than being silently dropped. The
        # skill validates full slice coverage before saving, so this is a conservative fallback.
        try:
            sd = ScreeningDecision(decisions.get(i, "borderline"))
        except ValueError:
            sd = ScreeningDecision.borderline
        candidate.screening_decision = sd
        if sd == ScreeningDecision.include:
            new_includes.append(candidate)
        elif sd == ScreeningDecision.borderline:
            new_borderlines.append(candidate)

    # Issue #49: enrich metadata for the new includes and borderlines so they
    # arrive at Stage 12 with authors/journal/year/abstract populated.
    new_cc_articles = new_includes + new_borderlines
    if new_cc_articles:
        cfg = load_api_config()
        crossref = CrossRefClient(mailto=cfg.crossref_mailto)
        openalex = OpenAlexClient(email=cfg.openalex_email)
        s2 = SemanticScholarClient(api_key=cfg.semantic_scholar_api_key)
        metadata_stats = asyncio.run(
            _enrich_missing_metadata(new_cc_articles, crossref, openalex, s2)
        )
    else:
        metadata_stats = _zero_metadata_stats()

    review = state_manager.load(review_dir)

    # Append citation-chased includes and borderlines to articles_screened.json
    # so Stage 9 retrieval list and full-text assessment pick them up.
    to_append = new_includes + new_borderlines
    if to_append:
        screened_path = review_dir / review.articles_screened_file
        existing_screened = (
            json.loads(screened_path.read_text()) if screened_path.exists() else []
        )
        existing_screened.extend(a.model_dump(mode="json") for a in to_append)
        screened_path.write_text(json.dumps(existing_screened, indent=2), encoding="utf-8")

    review.status = ReviewStatus.citation_chased
    review.prisma_counts.citation_chasing_identified = backward_count + forward_count
    review.prisma_counts.citation_chasing_screened = len(all_candidates)
    review.prisma_counts.citation_chasing_added = len(new_includes)
    review.prisma_counts.citation_chasing_borderlines = len(new_borderlines)
    state_manager.save(review, review_dir)

    p_terms_str = ", ".join(filter_info.get("p_terms", [])) or "n/a"
    i_terms_str = ", ".join(filter_info.get("i_terms", [])) or "n/a"
    log_entry = (
        f"\n## Citation Chasing — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\n"
        f"- Backward (CrossRef references): {backward_count} candidate DOIs found\n"
        f"- Forward (Semantic Scholar citations): {forward_count} citing papers found\n"
        f"- After deduplication: {pre_filter_total} unique new candidates\n"
        f"- Keyword pre-filter (P AND I): {pre_filtered_count} dropped, "
        f"{pre_filter_total - pre_filtered_count} passed to LLM screening\n"
        f"  - P-terms: {p_terms_str}\n"
        f"  - I-terms: {i_terms_str}\n"
        f"- LLM-screened candidates: {len(all_candidates)}\n"
        f"- New includes from citation chasing: {len(new_includes)}\n"
        f"- New borderlines from citation chasing: {len(new_borderlines)}\n"
    )
    _append_search_log(review_dir, log_entry)

    candidates_path.unlink()

    print(prepare_output({
        "backward_candidates": backward_count,
        "forward_candidates": forward_count,
        "after_dedup": len(all_candidates),
        "new_includes": len(new_includes),
        "new_borderlines": len(new_borderlines),
        "metadata_enrichment": metadata_stats,
    }))


def enrich_cc(review_dir: Path) -> None:
    """Enrich identifiers + metadata for already-saved CC articles in articles_screened.json.

    Pass 1: recover missing DOI/PMID via CrossRef → S2 → PubMed for articles with neither.
    Pass 2: fill missing authors/journal/year/abstract via CrossRef → OpenAlex → S2 for all
    CC articles (including those whose identifier was just recovered in Pass 1).

    Safe to re-run; does not change review status.
    """
    review = state_manager.load(review_dir)
    screened_path = review_dir / review.articles_screened_file
    if not screened_path.exists():
        die(f"{review.articles_screened_file} not found.")

    all_screened = [Article(**a) for a in json.loads(screened_path.read_text())]
    cc_articles = [a for a in all_screened if a.citation_chased]
    if not cc_articles:
        print(prepare_output({"message": "No CC articles found.", "enrichment": {}, "metadata_enrichment": {}}))
        return

    cfg = load_api_config()
    crossref = CrossRefClient(mailto=cfg.crossref_mailto)
    s2 = SemanticScholarClient(api_key=cfg.semantic_scholar_api_key)
    pubmed = PubMedClient(api_key=cfg.ncbi_api_key)
    openalex = OpenAlexClient(email=cfg.openalex_email)

    # Pass 1: recover missing identifiers (DOI/PMID).
    targets = [a for a in cc_articles if not a.doi and not a.pmid]
    if targets:
        stats = asyncio.run(_enrich_missing_identifiers(targets, crossref, s2, pubmed))
    else:
        stats = {
            "candidates_missing_ids": 0,
            "recovered_by_crossref": 0,
            "recovered_by_s2": 0,
            "recovered_by_pubmed": 0,
            "still_missing": 0,
        }

    # Pass 2: enrich metadata (authors/journal/year/abstract) on ALL CC articles
    # (including ones whose identifier was just recovered).
    metadata_stats = asyncio.run(
        _enrich_missing_metadata(cc_articles, crossref, openalex, s2)
    )

    screened_path.write_text(
        json.dumps([a.model_dump(mode="json") for a in all_screened], indent=2),
        encoding="utf-8",
    )

    log_entry = (
        f"\n## CC Identifier + Metadata Enrichment — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\n"
        f"- CC articles with missing identifiers: {stats['candidates_missing_ids']}\n"
        f"- Recovered by CrossRef: {stats['recovered_by_crossref']}\n"
        f"- Recovered by S2: {stats['recovered_by_s2']}\n"
        f"- Recovered by PubMed: {stats['recovered_by_pubmed']}\n"
        f"- Still missing identifiers: {stats['still_missing']}\n"
        f"- Metadata enriched by CrossRef: {metadata_stats['enriched_by_crossref']}\n"
        f"- Metadata enriched by OpenAlex: {metadata_stats['enriched_by_openalex']}\n"
        f"- Metadata enriched by S2: {metadata_stats['enriched_by_s2']}\n"
        f"- Still metadata-incomplete: {metadata_stats['still_incomplete']}\n"
    )
    _append_search_log(review_dir, log_entry)

    print(prepare_output({
        "enrichment": stats,
        "metadata_enrichment": metadata_stats,
    }))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Citation chasing — backward and forward")
    parser.add_argument("--review-dir", required=True, metavar="DIR")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--save", action="store_true")
    mode.add_argument("--enrich-cc", action="store_true",
                      help="Enrich identifiers for already-saved CC articles (post-save remediation)")
    parser.add_argument("--input-json", metavar="JSON")
    parser.add_argument("--slice-of", type=int, default=None, metavar="N")
    parser.add_argument("--slice-index", type=int, default=None, metavar="I")
    args = parser.parse_args()

    review_dir = Path(args.review_dir)
    if args.prepare:
        prepare(review_dir, args.slice_of, args.slice_index)
    elif args.enrich_cc:
        enrich_cc(review_dir)
    else:
        if not args.input_json:
            die("--save requires --input-json")
        save(review_dir, args.input_json)
