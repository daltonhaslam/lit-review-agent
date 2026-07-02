"""Generate a retrieval list CSV for hospital library submission.

Runs after citation chasing (Stage 8.5). Includes all articles with
screening_decision of include or borderline from articles_screened.json,
which now contains both database-screened articles AND citation-chased includes.
This produces a single complete retrieval list for all articles in one library request.

Columns: title, authors, year, journal, doi, pmid, oa_status, oa_pdf_url,
         screening_decision, notes

Input:  review_dir (reads articles_screened.json; prior status must be citation_chased)
Output: retrieval_list.csv; status=retrieval_list_generated
CLI:    python -m src.skills.retrieval_list_generator --review-dir reviews/{slug}
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.agent import state as state_manager
from src.models.article import Article, ScreeningDecision
from src.models.review import ReviewStatus

RETRIEVAL_COLUMNS = [
    "title", "authors", "year", "journal", "doi", "pmid",
    "oa_status", "oa_pdf_url", "screening_decision", "notes",
]


def generate(review_dir: Path) -> dict:
    review = state_manager.load(review_dir)
    if review.status != ReviewStatus.citation_chased:
        raise ValueError(f"Expected status 'citation_chased', got '{review.status}'.")

    articles = [
        Article(**a)
        for a in json.loads((review_dir / review.articles_screened_file).read_text())
    ]

    eligible = [
        a for a in articles
        if a.screening_decision in (ScreeningDecision.include, ScreeningDecision.borderline)
    ]
    # include first, then borderline; each group sorted by title
    eligible.sort(key=lambda a: (a.screening_decision != ScreeningDecision.include, a.title))

    csv_path = review_dir / "retrieval_list.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=RETRIEVAL_COLUMNS)
        writer.writeheader()
        for article in eligible:
            author_str = "; ".join(article.authors[:3])
            if len(article.authors) > 3:
                author_str += " et al."
            writer.writerow({
                "title": article.title,
                "authors": author_str,
                "year": article.year or "",
                "journal": article.journal or "",
                "doi": article.doi or "",
                "pmid": article.pmid or "",
                "oa_status": {True: "OA", False: "Not OA"}.get(article.oa_status, ""),
                "oa_pdf_url": article.oa_pdf_url or "",
                "screening_decision": article.screening_decision.value,
                "notes": (
                    "Borderline — requires full-text review"
                    if article.screening_decision == ScreeningDecision.borderline
                    else "From citation chasing"
                    if article.citation_chased
                    else ""
                ),
            })

    review.status = ReviewStatus.retrieval_list_generated
    state_manager.save(review, review_dir)

    include_count = sum(1 for a in eligible if a.screening_decision == ScreeningDecision.include)
    oa_count = sum(1 for a in eligible if a.oa_status)

    return {
        "total": len(eligible),
        "include": include_count,
        "borderline": len(eligible) - include_count,
        "open_access": oa_count,
        "csv_path": str(csv_path),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate retrieval list CSV for hospital library")
    parser.add_argument("--review-dir", required=True, metavar="DIR")
    args = parser.parse_args()

    try:
        result = generate(Path(args.review_dir))
        print(json.dumps(result, indent=2))
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)
