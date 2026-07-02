"""Batch C — query_builder._resolve_refs / _flatten_and_clean two-digit back-reference bug.

Regression test: the naive q.replace(ref, ...) in _resolve_refs corrupts two-digit
#N back-references (#10/#11/#12) because replacing "#1" also matches the "#1" prefix
inside "#10". This drops one PICO concept from the primary PubMed query for any
multi-concept review (>= 10 steps).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.skills import query_builder
from src.skills.query_builder import _flatten_and_clean


class TestTwoDigitBackReferences:
    def test_final_step_references_two_digit_step(self):
        # 10-step set; final step references the two-digit #10.
        steps = [
            {"step": "#1", "query": '"HF"[Mesh]'},
            {"step": "#2", "query": "hf*[tiab]"},
            {"step": "#3", "query": "#1 OR #2"},
            {"step": "#4", "query": '"Mobility"[Mesh]'},
            {"step": "#5", "query": "mobility*[tiab]"},
            {"step": "#6", "query": "#4 OR #5"},
            {"step": "#7", "query": '"Readmission"[Mesh]'},
            {"step": "#8", "query": "readmission*[tiab]"},
            {"step": "#9", "query": "#7 OR #8"},
            {"step": "#10", "query": "#9 AND Humans[Mesh]"},
        ]
        result, _ = _flatten_and_clean(steps)
        # #10 must resolve to the #9-family concept, NOT to (#1 expansion) + stray "0".
        assert "Readmission" in result
        assert "readmission" in result
        # No stray digit / no unresolved ref markers.
        assert "#" not in result

    def test_four_concept_pico_final_references_twelfth_step(self):
        # 12-step, 4-concept PICO final combine referencing #3, #6, #9, #12.
        steps = [
            {"step": "#1", "query": '"Pop"[Mesh]'},
            {"step": "#2", "query": "pop*[tiab]"},
            {"step": "#3", "query": "#1 OR #2"},
            {"step": "#4", "query": '"Interv"[Mesh]'},
            {"step": "#5", "query": "interv*[tiab]"},
            {"step": "#6", "query": "#4 OR #5"},
            {"step": "#7", "query": '"Compar"[Mesh]'},
            {"step": "#8", "query": "compar*[tiab]"},
            {"step": "#9", "query": "#7 OR #8"},
            {"step": "#10", "query": '"OutcomeTerm"[Mesh]'},
            {"step": "#11", "query": "outcometerm*[tiab]"},
            {"step": "#12", "query": "#10 OR #11"},
            {"step": "#13", "query": "(#3 AND #6 AND #9 AND #12) AND Humans[Mesh] AND English[lang]"},
        ]
        result, _ = _flatten_and_clean(steps)
        # The #12 (outcome) concept must survive — this is what the naive replace drops.
        assert "OutcomeTerm" in result
        assert "outcometerm" in result
        # All PICO concepts present.
        assert "Pop" in result
        assert "Interv" in result
        assert "Compar" in result
        # No unresolved refs and no stray digit corruption (e.g. "(...)2" from #12 -> #1).
        assert "#" not in result
        # Parentheses stay balanced (a stray digit between close-parens would trip the cleaner).
        assert result.count("(") == result.count(")")

    def test_resolve_refs_does_not_corrupt_ten(self):
        # Direct _resolve_refs check: #10 must not degrade to (#1 flat) + "0".
        resolved = {"#1": "AAA", "#10": "ZZZ"}
        out = query_builder._resolve_refs("#10", resolved)
        assert out == "(ZZZ)"

    def test_resolve_refs_tolerates_malformed_step_key(self):
        # Step keys come from LLM JSON output; a malformed key (not "#<int>") must NOT crash
        # _resolve_refs (an int-parsing sort key would raise ValueError and strand the review).
        resolved = {"#1": "AAA", "bogus": "BBB"}
        out = query_builder._resolve_refs("#1 AND Humans[Mesh]", resolved)
        assert out == "(AAA) AND Humans[Mesh]"  # #1 resolved, malformed key ignored, no crash
