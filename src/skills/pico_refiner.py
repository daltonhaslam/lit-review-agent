"""Refine a research question into structured PICO(S) using Fundamental Four answers.

Subscription-based version: no Anthropic API calls.
--prepare: formats question + review_type + Fundamental Four answers as JSON for Claude.
--save:    accepts Claude's PICO JSON, validates, writes to state.

Input:  --review-dir DIR (reads raw_question + review_type from state), --answers-json JSON
Output: JSON to stdout
Side effect (--save): writes PICO to state.json; sets status = pico_formulated
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.agent import state as state_manager
from src.models.pico import PICO, FundamentalFour
from src.models.review import ReviewStatus
from src.skills._base import die, parse_save_input, prepare_output

PICO_TASK = (
    "Build a rigorous PICO(S) framework grounded in the Fundamental Four answers. "
    "P = specific target population (setting, condition, age, comorbidities as relevant). "
    "I = specific intervention/exposure. "
    "C = comparator (usual care, alternative, or absent for scoping reviews). "
    "O = all clinically relevant outcomes as a LIST — primary first, be specific "
    '(e.g., "30-day all-cause readmission rate" not "readmission"). '
    "S = study design restriction or null. "
    "Inclusion/exclusion criteria must be PICO-derived and specific. "
    'Respond with JSON only: {"pico": {"P": "...", "I": "...", "C": "...", '
    '"O": ["primary outcome", "secondary outcome"], "S": null}, '
    '"inclusion_criteria": ["criterion"], "exclusion_criteria": ["criterion"], '
    '"fundamental_four": {"what_we_know": "...", "what_we_dont_know": "...", '
    '"what_we_should_know": "...", "why_we_should_know": "..."}}'
)


def prepare(answers: dict, review_dir: Path) -> None:
    """Print JSON context for Claude to build PICO(S)."""
    review = state_manager.load(review_dir)
    data = {
        "question": review.raw_question,
        "review_type": review.review_type.value if review.review_type else "systematic",
        "fundamental_four": {
            "what_we_know": answers.get("what_we_know", ""),
            "what_we_dont_know": answers.get("what_we_dont_know", ""),
            "what_we_should_know": answers.get("what_we_should_know", ""),
            "why_we_should_know": answers.get("why_we_should_know", ""),
        },
        "task": PICO_TASK,
    }
    print(prepare_output(data))


def save(data: dict, review_dir: Path) -> None:
    """Validate Claude's PICO output and write to state."""
    pico_data = data.get("pico", {})

    # Ensure O is always a list
    if isinstance(pico_data.get("O"), str):
        pico_data["O"] = [pico_data["O"]]

    ff_data = data.get("fundamental_four", {})
    review = state_manager.load(review_dir)
    review.pico = PICO(
        P=pico_data.get("P", ""),
        I=pico_data.get("I", ""),
        C=pico_data.get("C", ""),
        O=pico_data.get("O", []),
        S=pico_data.get("S"),
        inclusion_criteria=data.get("inclusion_criteria", []),
        exclusion_criteria=data.get("exclusion_criteria", []),
        fundamental_four=FundamentalFour(
            what_we_know=ff_data.get("what_we_know", ""),
            what_we_dont_know=ff_data.get("what_we_dont_know", ""),
            what_we_should_know=ff_data.get("what_we_should_know", ""),
            why_we_should_know=ff_data.get("why_we_should_know", ""),
        ),
        raw_question=review.raw_question,
    )
    # Monotonic guard: only advance the pipeline pointer forward. An out-of-order
    # re-save at a later status must update the PICO without regressing status
    # (which would orphan completed downstream work).
    if review.status.at_or_before(ReviewStatus.pico_formulated):
        review.status = ReviewStatus.pico_formulated
    state_manager.save(review, review_dir)

    print(prepare_output({"status": review.status.value, "pico": pico_data}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Refine research question into PICO(S)")
    parser.add_argument("--review-dir", required=True, metavar="DIR", help="Review directory")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true", help="Format context JSON for Claude")
    mode.add_argument("--save", action="store_true", help="Save Claude's PICO to state")
    parser.add_argument(
        "--answers-json",
        metavar="JSON",
        help='Fundamental Four answers JSON (required with --prepare): '
             '{"what_we_know": "...", "what_we_dont_know": "...", '
             '"what_we_should_know": "...", "why_we_should_know": "..."}',
    )
    parser.add_argument("--input-json", metavar="JSON", help="Claude's PICO output JSON (required with --save)")
    args = parser.parse_args()

    review_dir = Path(args.review_dir)

    if args.prepare:
        if not args.answers_json:
            die("--answers-json is required with --prepare")
        try:
            answers = json.loads(args.answers_json)
            prepare(answers, review_dir)
        except json.JSONDecodeError as exc:
            die(f"Invalid --answers-json: {exc}")
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
