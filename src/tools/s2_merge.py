"""One-shot: fetch Semantic Scholar results and merge into an existing articles_raw.json.

Usage:
    python3 -m src.tools.s2_merge \
        --review-dir reviews/subspecialty-tele-consult-2026-05 \
        --year-from 2015 \
        --max 500

Reads the S2 query from state.json, fetches results, deduplicates against
existing articles_raw.json by s2_id + DOI, appends new records, and saves.
Does NOT update review status — this is a surgical patch before aggregation.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.agent import state as state_manager
from src.integrations.config import load_api_config
from src.integrations.semantic_scholar_client import SemanticScholarClient


async def _run(review_dir: Path, year_from: int, max_results: int) -> None:
    review = state_manager.load(review_dir)
    cfg = load_api_config()

    if not review.queries or not review.queries.semantic_scholar:
        print("ERROR: no semantic_scholar query in state.json", file=sys.stderr)
        sys.exit(1)

    query = review.queries.semantic_scholar[0].query_string
    year_range = (year_from, datetime.now().year)

    print(f"Query : {query}", file=sys.stderr)
    print(f"Years : {year_range[0]}–{year_range[1]}", file=sys.stderr)
    print(f"Key   : {'set' if cfg.semantic_scholar_api_key else 'NOT SET'}", file=sys.stderr)

    client = SemanticScholarClient(api_key=cfg.semantic_scholar_api_key)
    new_articles = await client.search(query, max_results=max_results, year_range=year_range)
    print(f"S2 returned: {len(new_articles)} articles", file=sys.stderr)

    raw_path = review_dir / review.articles_raw_file
    existing = json.loads(raw_path.read_text()) if raw_path.exists() else []

    existing_ids: set[str] = set()
    for a in existing:
        if a.get("s2_id"):
            existing_ids.add(a["s2_id"])
        if a.get("doi"):
            existing_ids.add(a["doi"].lower())

    added = 0
    for a in new_articles:
        a_dict = a.model_dump(mode="json")
        s2_id = a_dict.get("s2_id") or ""
        doi = (a_dict.get("doi") or "").lower()
        if s2_id in existing_ids or (doi and doi in existing_ids):
            continue
        existing.append(a_dict)
        if s2_id:
            existing_ids.add(s2_id)
        if doi:
            existing_ids.add(doi)
        added += 1

    raw_path.write_text(json.dumps(existing, indent=2))
    print(
        json.dumps({
            "s2_fetched": len(new_articles),
            "new_added": added,
            "total_raw": len(existing),
        }, indent=2)
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--review-dir", required=True)
    parser.add_argument("--year-from", type=int, default=2015)
    parser.add_argument("--max", type=int, default=500)
    args = parser.parse_args()
    asyncio.run(_run(Path(args.review_dir), args.year_from, args.max))
