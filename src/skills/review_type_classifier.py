"""Classify a raw research question into the appropriate review type.

Subscription-based version: no Anthropic API calls.
--prepare: formats question + review type context as JSON for Claude to classify in-conversation.
--save:    accepts Claude's classification JSON, validates, writes to state.

Input:  --review-dir DIR (reads raw_question from state)
Output: JSON to stdout
Side effect (--save): updates state.json with review_type + status
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.agent import state as state_manager
from src.models.review import ReviewStatus, ReviewType
from src.skills._base import die, parse_save_input, prepare_output

REVIEW_TYPE_OPTIONS = {
    "narrative": (
        "Quick evidence summary for decision-making (C-suite, staff education). "
        "No exhaustive search required. Use when speed > completeness."
    ),
    "scoping": (
        "Map the size and nature of the evidence base; identify gaps; "
        "determine whether a full systematic review is warranted. Use for exploratory questions."
    ),
    "systematic": (
        "Rigorous, exhaustive answer to a specific clinical/operational question. "
        "Required when evidence quality and completeness matter "
        "(quality committees, Joint Commission standards, mortality index work)."
    ),
    "umbrella": "Summarize and compare findings from multiple existing systematic reviews on a broad topic.",
    "realist": "Explain HOW and WHY an intervention works in different contexts (implementation science).",
    "critical": "Evaluate contradictions and methodological problems in the literature.",
}

DEPTH_BY_TYPE = {
    "narrative": "representative",
    "scoping": "comprehensive",
    "systematic": "exhaustive",
    "umbrella": "exhaustive (systematic reviews only)",
    "realist": "iterative",
    "critical": "representative",
}


def prepare(review_dir: Path) -> None:
    """Print JSON context for Claude to classify the review type."""
    review = state_manager.load(review_dir)
    data = {
        "question": review.raw_question,
        "review_type_options": REVIEW_TYPE_OPTIONS,
        "depth_by_type": DEPTH_BY_TYPE,
        "task": (
            "Classify this research question into one of the review types listed above. "
            'Respond with JSON only: {"review_type": "<type>", "rationale": "<1-2 sentence rationale>"}'
        ),
    }
    print(prepare_output(data))


def save(data: dict, review_dir: Path) -> None:
    """Validate Claude's classification and write to state."""
    raw_type = data.get("review_type", "")
    try:
        review_type = ReviewType(raw_type)
    except ValueError:
        review_type = ReviewType.narrative

    review = state_manager.load(review_dir)
    review.review_type = review_type
    # Monotonic guard: only advance the pipeline pointer forward. An out-of-order
    # re-save at a later status must update review_type without regressing status
    # (which would orphan completed downstream work).
    if review.status.at_or_before(ReviewStatus.review_type_classified):
        review.status = ReviewStatus.review_type_classified
    state_manager.save(review, review_dir)

    print(prepare_output({
        "review_type": review_type.value,
        "rationale": data.get("rationale", ""),
        "status": review.status.value,
    }))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Classify a research question into a review type")
    parser.add_argument("--review-dir", required=True, metavar="DIR", help="Review directory")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true", help="Format context JSON for Claude")
    mode.add_argument("--save", action="store_true", help="Save Claude's classification to state")
    parser.add_argument("--input-json", metavar="JSON", help="Claude's output JSON (required with --save)")
    args = parser.parse_args()

    review_dir = Path(args.review_dir)

    if args.prepare:
        try:
            prepare(review_dir)
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
