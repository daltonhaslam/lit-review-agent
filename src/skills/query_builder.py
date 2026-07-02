"""Build MeSH + free-text search queries from PICO(S) and save to state.

Subscription-based version: MeSH lookups (PubMed API) run in --prepare; no Anthropic API calls.
--prepare: runs parallel MeSH lookups, builds stepwise PubMed query history, formats all data for Claude
           to generate openalex_query and semantic_scholar_query and optionally refine pubmed_steps.
--save:    accepts Claude's query JSON, builds QuerySet, writes to state.

Process (--prepare):
  1. PubMed MeSH lookup → preferred name, entry terms (synonyms), year introduced
  2. MeSH query: "Preferred Name"[Mesh]
  3. Free-text query: main_term*[tiab] OR synonym*[tiab] ... (from MeSH entry terms)
  4. Combine with OR; assign stepwise #n numbers
  5. Final: combine all aspects with AND + add standard filters

Input:  --review-dir DIR (reads PICO from state)
Output: JSON to stdout
Side effect (--save): updates state.json queries field; sets status = queries_built
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.agent import state as state_manager
from src.integrations.config import load_api_config
from src.integrations.pubmed_client import PubMedClient
from src.models.query import Query, QuerySet
from src.models.review import ReviewStatus
from src.skills._base import die, parse_save_input, prepare_output
from src.skills._keywords import _STOP_WORDS
from src.utils.rate_limiter import RateLimiter

# Warn when a MeSH term was introduced recently — older literature won't be indexed under it
MESH_NEW_YEAR_THRESHOLD = 5

QUERY_PROMPT = (
    "Given the PICO and the PubMed stepwise query history already built from MeSH lookups, "
    "generate keyword query strings for OpenAlex, Semantic Scholar, and Europe PMC. "
    "You may also adjust pubmed_steps if needed, but return them in full. "
    "If skipped_fields is non-empty, you must build appropriate PubMed steps for those aspects "
    "yourself (using MeSH headings you know plus [tiab] free-text), and integrate them into "
    "pubmed_steps — preserve #N references emitted by pubmed_steps_from_mesh for any aspects "
    "that were auto-built.\n\n"
    "Query format requirements:\n"
    "- openalex_query: plain Boolean keyword string (AND/OR/NOT allowed; no MeSH tags like [Mesh], [tiab], [pt])\n"
    "- europepmc_query: plain Boolean keyword string (AND/OR/NOT allowed; no MeSH tags)\n"
    "- semantic_scholar_query: natural-language phrase ONLY — no AND/OR/NOT, no parentheses, "
    "no field tags. Write it as a short phrase a human would type into a search bar. "
    'Example: "pediatric observation hospitalization children hospital trend"\n\n'
    'Respond with JSON only: {"pubmed_steps": [...], "openalex_query": "...", '
    '"semantic_scholar_query": "...", "europepmc_query": "..."}'
)


def _steps_from_mesh(
    mesh: dict,
    term: str,
    aspect_label: str,
    step_num: int,
    current_year: int,
) -> tuple[list[dict], list[str], int]:
    """Build PubMed steps from a MeSH lookup result."""
    steps: list[dict] = []
    warnings: list[str] = []

    if mesh.get("found"):
        preferred = mesh["preferred_name"]
        entry_terms: list[str] = mesh.get("entry_terms", [])
        year_introduced: int | None = mesh.get("year_introduced")

        if year_introduced and (current_year - year_introduced) < MESH_NEW_YEAR_THRESHOLD:
            warnings.append(
                f"MeSH '{preferred}' introduced {year_introduced} — "
                f"free-text search recommended for pre-{year_introduced} literature."
            )

        steps.append({
            "step": f"#{step_num}",
            "query": f'"{preferred}"[Mesh]',
            "rationale": f"{aspect_label} — MeSH controlled vocabulary",
        })
        step_num += 1

        free_text_terms = [term] + [t for t in entry_terms if t.lower() != term.lower()]
        ft_parts = [f'{t.lower()}*[tiab]' for t in free_text_terms[:6]]
        steps.append({
            "step": f"#{step_num}",
            "query": " OR ".join(ft_parts),
            "rationale": f"{aspect_label} — free-text with MeSH synonyms",
        })
        step_num += 1

        steps.append({
            "step": f"#{step_num}",
            "query": f"#{step_num - 2} OR #{step_num - 1}",
            "rationale": f"{aspect_label} — MeSH OR free-text (broadest recall)",
        })
        step_num += 1

    else:
        warnings.append(f"No MeSH term found for '{term}' — using free-text search only.")
        steps.append({
            "step": f"#{step_num}",
            "query": f"{term.lower()}*[tiab]",
            "rationale": f"{aspect_label} — free-text only (no MeSH match)",
        })
        step_num += 1

    return steps, warnings, step_num


def _extract_concepts(field_text: str) -> list[str]:
    """Extract up to 4 concept terms from a long PICO sentence for MeSH lookup.

    Forms bigrams from strictly adjacent non-stopword tokens first (better MeSH hits),
    then fills remaining slots with single terms.
    """
    text = re.sub(r"\([^)]*\)", " ", field_text)
    text = re.sub(r"[<>=—–\-/]", " ", text)

    tokens = re.findall(r"[A-Za-z]{4,}", text)
    tokens_lower = [t.lower() for t in tokens]
    non_stop_pos = [i for i, t in enumerate(tokens_lower) if t not in _STOP_WORDS]

    concepts: list[str] = []
    used: set[int] = set()

    # Bigrams: strictly adjacent non-stopword tokens (p2 - p1 == 1 means no stopword gap)
    for j in range(len(non_stop_pos) - 1):
        p1, p2 = non_stop_pos[j], non_stop_pos[j + 1]
        if p2 - p1 == 1 and p2 not in used:
            concepts.append(f"{tokens_lower[p1]} {tokens_lower[p2]}")
            used.add(p1)
            used.add(p2)
        if len(concepts) >= 2:
            break

    seen = {w for phrase in concepts for w in phrase.split()}
    for p in non_stop_pos:
        w = tokens_lower[p]
        if p not in used and w not in seen:
            concepts.append(w)
            seen.add(w)
            used.add(p)
        if len(concepts) >= 4:
            break

    return concepts[:4]


async def _run_prepare(review_dir: Path) -> None:
    """Run MeSH lookups and print JSON context for Claude."""
    review = state_manager.load(review_dir)
    if review.pico is None:
        raise ValueError("PICO must be formulated before building queries.")

    config = load_api_config()
    pubmed_client = PubMedClient(api_key=config.ncbi_api_key)
    pico = review.pico
    current_year = dt.datetime.now(dt.timezone.utc).year

    pic_terms = [(label, term) for label, term in [
        ("Population (P)", pico.P),
        ("Intervention (I)", pico.I),
        ("Comparator (C)", pico.C),
    ] if term]
    outcome_terms = [(f"Outcome ({o})", o) for o in pico.O[:3] if o]
    all_terms = pic_terms + outcome_terms

    pic_label_set = {label for label, _ in pic_terms}
    skipped_fields: list[dict] = []
    all_warnings: list[str] = []
    short_terms: list[tuple[str, str]] = []
    long_terms: list[tuple[str, str]] = []

    for label, term in all_terms:
        if len(term.split()) > 3:
            long_terms.append((label, term))
        else:
            short_terms.append((label, term))

    # Parallel MeSH lookups for short terms
    short_mesh_results = await asyncio.gather(
        *[RateLimiter.retry_with_backoff(lambda t=t: pubmed_client.mesh_lookup(t)) for _, t in short_terms],
        return_exceptions=True,
    )

    step_num = 1
    all_steps: list[dict] = []
    aspect_combine_refs: list[str] = []
    outcome_refs: list[str] = []

    for i, (label, term) in enumerate(short_terms):
        mesh = short_mesh_results[i]
        if isinstance(mesh, Exception):
            mesh = {"term": term, "found": False}
            all_warnings.append(f"MeSH lookup failed for '{term}'.")

        steps, warnings, step_num = _steps_from_mesh(mesh, term, label, step_num, current_year)
        all_steps.extend(steps)
        all_warnings.extend(warnings)

        if label in pic_label_set:
            aspect_combine_refs.append(steps[-1]["step"])
        else:
            outcome_refs.append(steps[-1]["step"])

    # Concept extraction + MeSH lookups for long fields
    for label, term in long_terms:
        concepts = _extract_concepts(term)
        if not concepts:
            skipped_fields.append({"label": label, "raw_term": term})
            all_warnings.append(
                f"PICO field '{label}' is a sentence ({len(term.split())} words); "
                "concept extraction returned no results — Claude must construct PubMed steps directly."
            )
            continue

        concept_mesh_results = await asyncio.gather(
            *[RateLimiter.retry_with_backoff(lambda c=c: pubmed_client.mesh_lookup(c)) for c in concepts],
            return_exceptions=True,
        )

        concept_combine_refs: list[str] = []
        for concept, mesh in zip(concepts, concept_mesh_results):
            if isinstance(mesh, Exception):
                mesh = {"term": concept, "found": False}
                all_warnings.append(f"MeSH lookup failed for extracted concept '{concept}' in '{label}'.")
            steps, warnings, step_num = _steps_from_mesh(
                mesh, concept, f"{label} — {concept}", step_num, current_year
            )
            all_steps.extend(steps)
            all_warnings.extend(warnings)
            concept_combine_refs.append(steps[-1]["step"])

        if len(concept_combine_refs) == 1:
            field_combine_ref = concept_combine_refs[0]
        else:
            all_steps.append({
                "step": f"#{step_num}",
                "query": " OR ".join(concept_combine_refs),
                "rationale": f"{label} — all extracted concepts (OR broadest recall)",
            })
            field_combine_ref = f"#{step_num}"
            step_num += 1

        if label in pic_label_set:
            aspect_combine_refs.append(field_combine_ref)
        else:
            outcome_refs.append(field_combine_ref)

        all_warnings.append(
            f"PICO field '{label}' is a sentence; extracted concepts for MeSH lookup: {concepts}. "
            "Claude may refine pubmed_steps if key concepts were missed."
        )

    if len(outcome_refs) > 1:
        all_steps.append({
            "step": f"#{step_num}",
            "query": " OR ".join(outcome_refs),
            "rationale": "Outcomes (O) — any specified outcome",
        })
        aspect_combine_refs.append(f"#{step_num}")
        step_num += 1
    elif outcome_refs:
        aspect_combine_refs.append(outcome_refs[0])

    filter_parts = ["Humans[Mesh]", "English[lang]"]
    if pico.S:
        design_filters = {
            "rct": "Randomized Controlled Trial[pt]",
            "systematic review": "Systematic Review[pt]",
            "meta-analysis": "Meta-Analysis[pt]",
        }
        for keyword, filter_str in design_filters.items():
            if keyword in pico.S.lower():
                filter_parts.append(filter_str)
                break

    if aspect_combine_refs:
        combined_aspects = " AND ".join(aspect_combine_refs)
        final_suffix = " (partial: skipped_fields not yet integrated)" if skipped_fields else ""
        final_query = f"({combined_aspects}) AND " + " AND ".join(filter_parts)
    else:
        final_suffix = " (partial: all PICO aspects skipped — Claude must construct full query)"
        final_query = " AND ".join(filter_parts)

    all_steps.append({
        "step": f"#{step_num}",
        "query": final_query,
        "rationale": "Final — all PICO aspects AND Humans AND English" + final_suffix,
    })

    data = {
        "pico": {"P": pico.P, "I": pico.I, "C": pico.C, "O": pico.O, "S": pico.S},
        "pubmed_steps_from_mesh": all_steps,
        "mesh_warnings": all_warnings,
        "skipped_fields": skipped_fields,
        "aspect_combine_refs": aspect_combine_refs,
        "outcome_refs": outcome_refs,
        "final_step_number": step_num,
        "task": QUERY_PROMPT,
    }
    print(prepare_output(data))


def _resolve_refs(q: str, resolved: dict[str, str]) -> str:
    # Substitute each #N with a trailing word boundary so "#1" cannot match the "#1" prefix
    # inside "#10"/"#11"/"#12" — a plain str.replace("#1", ...) silently drops the two-digit
    # concept, corrupting any query with >= 10 steps. The \b makes this order-independent and,
    # unlike an int-sorted key, it degrades gracefully on a malformed (non-"#N") LLM step key.
    for ref, flat in resolved.items():
        q = re.sub(re.escape(ref) + r"\b", f"({flat})", q)
    return q


def _flatten_and_clean(steps: list[dict]) -> tuple[str, list[str]]:
    """Resolve #N back-references into a flat query; check balance and strip stray chars."""
    resolved: dict[str, str] = {}
    for s in steps[:-1]:
        resolved[s["step"]] = _resolve_refs(s["query"], resolved)
    query = _resolve_refs(steps[-1]["query"], resolved)

    clean_warnings: list[str] = []

    def _strip_stray(m: re.Match) -> str:
        char = m.group(1)
        clean_warnings.append(f"Stripped stray '{char}' between close-parens")
        return "))"

    query = re.sub(r"\)([^A-Za-z()\s])\)", _strip_stray, query)

    if query.count("(") != query.count(")"):
        clean_warnings.append("Final PubMed query has unbalanced parentheses; manual review required")

    return query, clean_warnings


def save(data: dict, review_dir: Path) -> None:
    """Build QuerySet from Claude's query JSON and write to state."""
    pubmed_steps = data.get("pubmed_steps", [])
    openalex_q = data.get("openalex_query", "")
    s2_q = data.get("semantic_scholar_query", "")
    europepmc_q = data.get("europepmc_query", "")

    query_objects = [
        Query(
            step_number=s["step"],
            query_string=s["query"],
            database="pubmed",
            notes=s.get("rationale", ""),
        )
        for s in pubmed_steps
    ]
    flatten_warnings: list[str] = []
    if query_objects:
        flat_q, flatten_warnings = _flatten_and_clean(pubmed_steps)
        last = query_objects[-1]
        query_objects[-1] = Query(
            step_number=last.step_number,
            query_string=flat_q,
            database="pubmed",
            notes=last.notes,
        )
    review = state_manager.load(review_dir)
    review.queries = QuerySet(
        pubmed=query_objects,
        openalex=[Query(step_number="#1", query_string=openalex_q, database="openalex")] if openalex_q else [],
        semantic_scholar=[Query(step_number="#1", query_string=s2_q, database="semantic_scholar")] if s2_q else [],
        europepmc=[Query(step_number="#1", query_string=europepmc_q, database="europepmc")] if europepmc_q else [],
    )
    review.status = ReviewStatus.queries_built
    state_manager.save(review, review_dir)

    print(prepare_output({
        "status": ReviewStatus.queries_built.value,
        "pubmed_steps_count": len(query_objects),
        "openalex_query": openalex_q,
        "semantic_scholar_query": s2_q,
        "europepmc_query": europepmc_q,
        "mesh_warnings": data.get("mesh_warnings", []) + flatten_warnings,
    }))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build MeSH + free-text search queries from PICO")
    parser.add_argument("--review-dir", required=True, metavar="DIR", help="Review directory")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true", help="Run MeSH lookups and format context JSON for Claude")
    mode.add_argument("--save", action="store_true", help="Save Claude's query JSON to state")
    parser.add_argument("--input-json", metavar="JSON", help="Claude's query output JSON (required with --save)")
    args = parser.parse_args()

    review_dir = Path(args.review_dir)

    if args.prepare:
        try:
            asyncio.run(_run_prepare(review_dir))
        except Exception as exc:
            die(str(exc))
    else:
        if not args.input_json:
            die("--input-json is required with --save")
        try:
            data = parse_save_input(args.input_json)
            save(data, review_dir)
        except ValueError as exc:
            die(str(exc))
        except Exception as exc:
            die(str(exc))
