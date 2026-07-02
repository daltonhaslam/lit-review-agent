"""Item A: fulltext_assessor --iteration-only — scoped full-text assessment for
Checkpoint-5 iteration includes that APPENDS survivors without re-assessing (or
destroying) the already-approved primary/CC included set."""
from __future__ import annotations

import json
from pathlib import Path

from src.agent import state as state_manager
from src.models.article import Article, ScreeningDecision
from src.models.pico import PICO
from src.models.review import PRISMACounts, Review, ReviewStatus, ReviewType
from src.skills import fulltext_assessor as fa


def _make(tmp_path: Path) -> Path:
    rd = tmp_path / "rv"
    rd.mkdir()
    (rd / "pdfs").mkdir()
    review = Review(
        review_id="rv",
        raw_question="q",
        review_type=ReviewType.systematic,
        status=ReviewStatus.pdfs_downloaded,
        pico=PICO(P="x", I="y", C="z", O=["o"], inclusion_criteria=["a"], exclusion_criteria=["b"]),
        prisma_counts=PRISMACounts(
            identified=100, screened=100, eligibility=2, fulltext_assessed=2,
            not_retrieved=0, included=2,
        ),
    )
    state_manager.save(review, rd)

    # articles_screened.json: 2 approved primary includes + 1 new iteration include (with a PDF).
    screened = [
        Article(pmid="1", title="Primary A", screening_decision=ScreeningDecision.include).model_dump(mode="json"),
        Article(pmid="2", title="Primary B", screening_decision=ScreeningDecision.include).model_dump(mode="json"),
        Article(pmid="9", title="Iteration New", screening_decision=ScreeningDecision.include,
                iteration_added=True).model_dump(mode="json"),
    ]
    (rd / "articles_screened.json").write_text(json.dumps(screened, indent=2))
    # articles_included.json: the 2 approved primaries already passed full text.
    included = [
        Article(pmid="1", title="Primary A", screening_decision=ScreeningDecision.include,
                fulltext_decision=ScreeningDecision.include).model_dump(mode="json"),
        Article(pmid="2", title="Primary B", screening_decision=ScreeningDecision.include,
                fulltext_decision=ScreeningDecision.include).model_dump(mode="json"),
    ]
    (rd / "articles_included.json").write_text(json.dumps(included, indent=2))
    return rd


class TestIterationOnlyPrepare:
    def test_prepare_emits_only_the_new_iteration_article(self, tmp_path, capsys, monkeypatch):
        rd = _make(tmp_path)
        (rd / "pdfs" / "9.pdf").write_bytes(b"%PDF-1.4 stub")
        monkeypatch.setattr(fa, "_extract_text_and_full", lambda p: ("body " * 200, "body " * 200))
        fa.prepare(rd, iteration_only=True)
        out = json.loads(capsys.readouterr().out)
        orig = [a["orig_idx"] for a in out["articles"]]
        assert orig == [2]  # only the iteration article (index 2 in articles_screened.json)
        assert out["total_eligible"] == 1


class TestIterationOnlySave:
    def test_save_appends_survivor_preserving_primaries(self, tmp_path, capsys):
        rd = _make(tmp_path)
        fa.save(rd, json.dumps({"decisions": [
            {"orig_idx": 2, "decision": "include", "rationale": "meets criteria", "study_design": "RCT"},
        ]}), iteration_only=True)
        included = json.loads((rd / "articles_included.json").read_text())
        pmids = sorted(a["pmid"] for a in included)
        assert pmids == ["1", "2", "9"]  # primaries preserved + iteration appended
        saved = state_manager.load(rd)
        assert saved.prisma_counts.included == 3          # 2 + 1
        assert saved.prisma_counts.fulltext_assessed == 3  # incremented, not overwritten
        assert saved.status == ReviewStatus.fulltext_assessed
        # the appended article keeps its iteration provenance flag
        assert next(a for a in included if a["pmid"] == "9")["iteration_added"] is True

    def test_save_excludes_iteration_article_that_fails_fulltext(self, tmp_path, capsys):
        rd = _make(tmp_path)
        fa.save(rd, json.dumps({"decisions": [
            {"orig_idx": 2, "decision": "exclude", "rationale": "wrong population", "study_design": "cohort"},
        ]}), iteration_only=True)
        included = json.loads((rd / "articles_included.json").read_text())
        assert sorted(a["pmid"] for a in included) == ["1", "2"]  # iteration article correctly dropped
        saved = state_manager.load(rd)
        assert saved.prisma_counts.included == 2  # unchanged; primaries preserved

    def test_excluded_iteration_article_not_reassessed_in_a_later_round(self, tmp_path, capsys):
        # An iteration article excluded at full text must NOT reappear when a subsequent iteration
        # round runs the scoped assessment again — its terminal decision is persisted to the
        # screened file, so the scoped eligible set is empty (no re-assess, no PRISMA double-count).
        rd = _make(tmp_path)
        fa.save(rd, json.dumps({"decisions": [
            {"orig_idx": 2, "decision": "exclude", "rationale": "wrong population", "study_design": "cohort"},
        ]}), iteration_only=True)
        review = state_manager.load(rd)
        assert fa._load_eligible(rd, review, iteration_only=True) == []
        # And the terminal decision was written back to articles_screened.json.
        screened = json.loads((rd / "articles_screened.json").read_text())
        assert next(a for a in screened if a["pmid"] == "9")["fulltext_decision"] == "exclude"
