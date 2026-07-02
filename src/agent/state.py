"""State management for lit-review reviews.

Provides:
  load(review_dir)  → Review
  save(review, review_dir)
  scan(reviews_dir) → list of in-progress review summaries

CLI: python src/agent/state.py --scan reviews/
"""
import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# Allow running as a script from project root
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.models.review import Review, ReviewStatus

_STATUS_RESUME_HINTS: dict[str, str] = {
    "citation_chased": (
        "Citation chasing complete. "
        "Resume at Stage 9 (Retrieval List Generation — combined database + citation-chased articles)."
    ),
}

# Output-stage artifacts. If any of these exist while status != complete, the
# recorded status has drifted behind the work actually done on disk — typically a
# status reset during development that was never re-advanced, or a final session
# whose --save never persisted. scan() surfaces this so a finished review is not
# silently offered for "resume." See building/KNOWN_ISSUES.md #64.
_TERMINAL_ARTIFACTS: tuple[str, ...] = ("executive_summary.md", "synthesis.json")


def _safe_review_dir(reviews_root: Path, slug: str) -> Path:
    """Resolve slug to a path and verify it stays inside reviews_root."""
    candidate = (reviews_root / slug).resolve()
    if not str(candidate).startswith(str(reviews_root.resolve())):
        raise ValueError(f"Review slug escapes reviews directory: {slug!r}")
    return candidate


def load(review_dir: Path) -> Review:
    """Load state.json from a review directory."""
    state_file = review_dir / "state.json"
    if not state_file.exists():
        raise FileNotFoundError(f"No state.json found in {review_dir}")
    data = json.loads(state_file.read_text())
    return Review.model_validate(data)


def save(review: Review, review_dir: Path) -> None:
    """Atomically write state.json (write temp → rename)."""
    review_dir.mkdir(parents=True, exist_ok=True)
    review.updated_at = datetime.now(timezone.utc)
    state_file = review_dir / "state.json"
    payload = review.model_dump_json(indent=2)

    # Atomic write: temp file in same dir, then rename
    fd, tmp_path = tempfile.mkstemp(dir=review_dir, suffix=".tmp")
    try:
        os.write(fd, payload.encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp_path, state_file)


def scan(reviews_dir: Path) -> list[dict]:
    """Scan reviews_dir for in-progress reviews; return summary dicts."""
    summaries = []
    if not reviews_dir.exists():
        return summaries

    for entry in sorted(reviews_dir.iterdir()):
        if not entry.is_dir():
            continue
        state_file = entry / "state.json"
        if not state_file.exists():
            continue
        try:
            _safe_review_dir(reviews_dir, entry.name)
            review = load(entry)
        except Exception:
            continue

        if review.status == ReviewStatus.complete:
            continue

        status_val = review.status.value
        summary: dict = {
            "review_id": review.review_id,
            "review_dir": str(entry),
            "status": status_val,
            "review_type": review.review_type.value if review.review_type else "unknown",
            "raw_question": review.raw_question[:80] + "..." if len(review.raw_question) > 80 else review.raw_question,
            "prisma_identified": review.prisma_counts.identified,
            "prisma_included": review.prisma_counts.included,
            "updated_at": review.updated_at.isoformat(),
        }
        if status_val in _STATUS_RESUME_HINTS:
            summary["resume_hint"] = _STATUS_RESUME_HINTS[status_val]

        present_artifacts = [a for a in _TERMINAL_ARTIFACTS if (entry / a).exists()]
        if present_artifacts:
            summary["artifact_desync"] = (
                f"terminal artifact(s) present ({', '.join(present_artifacts)}) "
                f"but status={status_val} — possible desync; verify before resuming"
            )

        summaries.append(summary)

    return summaries


def _print_scan(reviews_dir: Path) -> None:
    summaries = scan(reviews_dir)
    if not summaries:
        print("No in-progress reviews found.")
        return

    print(f"Found {len(summaries)} in-progress review(s):\n")
    for s in summaries:
        print(f"  ID:       {s['review_id']}")
        print(f"  Stage:    {s['status']}")
        print(f"  Type:     {s['review_type']}")
        print(f"  Question: {s['raw_question']}")
        print(f"  PRISMA:   identified={s['prisma_identified']}  included={s['prisma_included']}")
        print(f"  Updated:  {s['updated_at']}")
        if s["status"] in _STATUS_RESUME_HINTS:
            print(f"  Resume:   {_STATUS_RESUME_HINTS[s['status']]}")
        if "artifact_desync" in s:
            print(f"  ⚠️ DESYNC: {s['artifact_desync']}")
        print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Lit Review state manager")
    parser.add_argument("--scan", metavar="REVIEWS_DIR", help="Scan for in-progress reviews")
    args = parser.parse_args()

    if args.scan:
        _print_scan(Path(args.scan))
    else:
        parser.print_help()
