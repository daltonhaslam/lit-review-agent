"""Generate a PROSPERO-style research protocol from PICO and lock it in state.

Subscription-based version: no Anthropic API calls.
--prepare: formats PICO + protocol structure instructions as JSON for Claude to write the protocol.
--save:    accepts Claude's protocol markdown (in {"protocol_text": "..."} JSON), writes to disk, locks state.

Input:  --review-dir DIR (reads PICO + review_type from state)
Output: JSON to stdout
Side effect (--save): writes reviews/{slug}/protocol.md; sets protocol_locked_at + status = protocol_generated
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.agent import state as state_manager
from src.models.review import ReviewStatus
from src.skills._base import die, parse_save_input, prepare_output

PROTOCOL_STRUCTURE = """\
Write a complete PROSPERO-style protocol — no generic placeholders. Use exactly this markdown structure:

# Protocol: [Descriptive title derived from PICO]

**Review ID:** [review_id]
**Date:** [today's date]
**Review Type:** [type]
**Status:** Pre-registered (institutional)

## 1. Background and Rationale
[2–3 paragraphs: clinical/operational context, why this question matters, gap in current evidence or practice.]

## 2. Objectives
[One PICO-framed objective sentence.]

## 3. Eligibility Criteria
### Inclusion Criteria
[Bulleted list — specific. Include population parameters, intervention definition, comparator, outcome requirements, language, date range.]
### Exclusion Criteria
[Bulleted list — specific exclusion rationale for each.]

## 4. Information Sources
[List databases based on review type:
- Narrative: PubMed/MEDLINE, OpenAlex
- Scoping: PubMed/MEDLINE, OpenAlex, Semantic Scholar, Europe PMC
- Systematic/Umbrella: PubMed/MEDLINE, OpenAlex, Semantic Scholar, Europe PMC; grey literature if applicable]

## 5. Search Strategy
Search strategy will be constructed after protocol approval using MeSH controlled vocabulary and free-text terms per PICO aspect. Full stepwise query history will be documented in `search_log.md` upon completion of Stage 4 (Query Construction).

## 6. Selection Process
[Describe dual-pass LLM abstract screening; human review of borderlines; full-text eligibility for all passing articles.]

### Citation Searching
Backward (reference lists) and forward (citing papers) citation searching will be conducted on abstract-screened includes prior to full-text retrieval. Citation-chased candidates will be title/abstract screened against PICO criteria; includes will be added to the retrieval list alongside database-search articles. This enables a single library retrieval request covering both streams.

## 7. Data Extraction Plan
[Fields: study design, N, follow-up duration, intervention details, comparator, primary outcome with effect size + 95% CI + p-value, secondary outcomes, author conclusions, funding source, conflict of interest.]

## 8. Quality Appraisal Plan
[Instrument selection by study type: AMSTAR-2 for systematic reviews, RoB 2.0 for RCTs, STROBE for observational, COREQ for qualitative. Narrative/scoping: descriptive only.]

## 9. Synthesis Method
[Narrative synthesis by outcome; meta-analysis if ≥3 studies report the same outcome with extractable data and I² < 60%.]

## 10. Protocol Registration
This protocol was registered internally prior to search commencement. For reviews intended for publication, PROSPERO registration is recommended.\
"""


class ProtocolLockedError(Exception):
    """Raised when attempting to regenerate a locked protocol."""


def _check_protocol_unlocked(review) -> None:
    """Raise ProtocolLockedError if the protocol has already been locked."""
    if review.protocol_locked_at is not None:
        raise ProtocolLockedError(
            f"Protocol already locked at {review.protocol_locked_at.isoformat()}. "
            "The protocol cannot be regenerated after locking."
        )


def prepare(review_dir: Path) -> None:
    """Print JSON context for Claude to write the protocol."""
    review = state_manager.load(review_dir)
    _check_protocol_unlocked(review)

    if review.pico is None:
        raise ValueError("PICO must be formulated before generating the protocol.")

    pico = review.pico
    data = {
        "review_id": review.review_id,
        "review_type": review.review_type.value if review.review_type else "systematic",
        "screening_mode": review.screening_mode.value,
        "raw_question": review.raw_question,
        "pico": {
            "P": pico.P,
            "I": pico.I,
            "C": pico.C,
            "O": pico.O,
            "S": pico.S,
        },
        "inclusion_criteria": pico.inclusion_criteria,
        "exclusion_criteria": pico.exclusion_criteria,
        "protocol_structure": PROTOCOL_STRUCTURE,
        "task": (
            "Write the full PROSPERO-style protocol following the structure above. "
            "Make every section concrete — no placeholder text. "
            'Return JSON only: {"protocol_text": "...full markdown protocol..."}'
        ),
    }
    print(prepare_output(data))


def save(data: dict, review_dir: Path) -> None:
    """Write protocol.md and lock the protocol timestamp in state."""
    review = state_manager.load(review_dir)
    _check_protocol_unlocked(review)

    protocol_text = data.get("protocol_text", "").strip()
    if not protocol_text:
        die("protocol_text is empty — Claude must return the full protocol markdown.")

    protocol_path = review_dir / "protocol.md"
    protocol_path.write_text(protocol_text, encoding="utf-8")

    review.protocol_locked_at = datetime.now(timezone.utc)
    review.status = ReviewStatus.protocol_generated
    state_manager.save(review, review_dir)

    print(prepare_output({
        "protocol_file": str(protocol_path),
        "protocol_locked_at": review.protocol_locked_at.isoformat(),
        "word_count": len(protocol_text.split()),
    }))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate PROSPERO-style protocol and lock it")
    parser.add_argument("--review-dir", required=True, metavar="DIR", help="Review directory")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true", help="Format context JSON for Claude")
    mode.add_argument("--save", action="store_true", help="Save Claude's protocol to disk and lock state")
    parser.add_argument("--input-json", metavar="JSON", help='Claude\'s output JSON with "protocol_text" key (required with --save)')
    args = parser.parse_args()

    review_dir = Path(args.review_dir)

    if args.prepare:
        try:
            prepare(review_dir)
        except ProtocolLockedError as exc:
            print(prepare_output({"error": "protocol_locked", "message": str(exc)}))
            sys.exit(2)
        except Exception as exc:
            die(str(exc))
    else:
        if not args.input_json:
            die("--input-json is required with --save")
        try:
            data = parse_save_input(args.input_json)
            save(data, review_dir)
        except ProtocolLockedError as exc:
            print(prepare_output({"error": "protocol_locked", "message": str(exc)}))
            sys.exit(2)
        except ValueError as exc:
            die(str(exc))
        except Exception as exc:
            die(str(exc))
