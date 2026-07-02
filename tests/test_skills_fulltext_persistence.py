"""Full-text excerpt persistence and source provenance.

Covers:
- Article model fields (`fulltext_excerpt`, `extraction_source`) round-trip + defaults.
- `_determine_extraction_source` preference order: full_text > abstract > tldr > none.
- `_format_article_text` emits the right body section for each source.
- `fulltext_assessor.prepare` writes excerpts back to articles_screened.json.
- `fulltext_assessor.persist_pdf_text` backfills excerpts onto already-completed reviews.
- `data_extractor.save` records `extraction_source` on each article.
- `evidence_table.csv` carries a `data_source` column with the right value.
- `synthesizer._build_synthesis_context` surfaces a Source line per article.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from src.agent import state as state_manager
from src.models.article import Article, ExtractionSource, ScreeningDecision
from src.models.pico import PICO
from src.models.query import Query, QuerySet
from src.models.review import Review, ReviewStatus, ReviewType, ScreeningMode
from src.skills import data_extractor as de_module
from src.skills import fulltext_assessor as fa_module
from src.skills.data_extractor import (
    _determine_extraction_source,
    _format_article_text,
    _write_evidence_table,
)
from src.skills.synthesizer import _build_synthesis_context


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_article(**kwargs) -> Article:
    defaults = dict(title="Sample article", year=2024, authors=["Smith J"])
    defaults.update(kwargs)
    return Article(**defaults)


def _make_review(tmp_path: Path, status: ReviewStatus = ReviewStatus.fulltext_assessed) -> tuple[Review, Path]:
    review_dir = tmp_path / "test-review"
    review_dir.mkdir()
    pico = PICO(P="P", I="I", C="C", O=["O"], inclusion_criteria=["inc"], exclusion_criteria=["exc"])
    review = Review(
        review_id="test-review",
        raw_question="q",
        review_type=ReviewType.systematic,
        screening_mode=ScreeningMode.thorough,
        pico=pico,
        queries=QuerySet(pubmed=[Query(step_number="#1", query_string="x", database="pubmed")]),
        status=status,
    )
    state_manager.save(review, review_dir)
    return review, review_dir


# ---------------------------------------------------------------------------
# Article model
# ---------------------------------------------------------------------------

class TestArticleFields:
    def test_fields_default_to_none(self):
        a = Article(title="x")
        assert a.fulltext_excerpt is None
        assert a.extraction_source is None

    def test_round_trip_through_json(self):
        a = Article(title="x", fulltext_excerpt="full text body", extraction_source="full_text")
        dumped = a.model_dump(mode="json")
        roundtripped = Article(**dumped)
        assert roundtripped.fulltext_excerpt == "full text body"
        assert roundtripped.extraction_source == "full_text"

    def test_loads_from_old_json_missing_fields(self):
        # Existing reviews on disk have JSON without these keys; loading must not fail.
        a = Article(**{"title": "old article"})
        assert a.fulltext_excerpt is None
        assert a.extraction_source is None

    def test_extraction_source_string_coerces_to_enum(self):
        # JSON files written before ExtractionSource was promoted to a StrEnum hold raw strings.
        a = Article(**{"title": "x", "extraction_source": "full_text"})
        assert a.extraction_source is ExtractionSource.full_text


# ---------------------------------------------------------------------------
# _determine_extraction_source
# ---------------------------------------------------------------------------

class TestDetermineExtractionSource:
    def test_full_text_wins_over_abstract(self):
        a = _make_article(fulltext_excerpt="pdf body", abstract="abs body", tldr="tldr body")
        assert _determine_extraction_source(a) == "full_text"

    def test_abstract_when_no_full_text(self):
        a = _make_article(abstract="abs body", tldr="tldr body")
        assert _determine_extraction_source(a) == "abstract"

    def test_tldr_when_neither_present(self):
        a = _make_article(tldr="tldr body")
        assert _determine_extraction_source(a) == "tldr"

    def test_none_when_all_empty(self):
        a = _make_article()
        assert _determine_extraction_source(a) == "none"

    def test_empty_string_excerpt_falls_through(self):
        # Truthy check: "" should not be treated as full text.
        a = _make_article(fulltext_excerpt="", abstract="abs body")
        assert _determine_extraction_source(a) == "abstract"


# ---------------------------------------------------------------------------
# _format_article_text — body selection by source
# ---------------------------------------------------------------------------

class TestFormatArticleTextBodySelection:
    def test_emits_full_text_section_when_excerpt_present(self):
        a = _make_article(fulltext_excerpt="Methods: this is the methods section.", abstract="Abstract body.")
        text = _format_article_text(a)
        assert "Full text (extracted from PDF):" in text
        assert "Methods: this is the methods section." in text
        # Abstract must NOT also be appended — full_text replaces it.
        assert "Abstract body." not in text

    def test_emits_abstract_when_no_excerpt(self):
        a = _make_article(abstract="Abstract body.")
        text = _format_article_text(a)
        assert "Abstract:" in text
        assert "Abstract body." in text

    def test_emits_tldr_when_neither_excerpt_nor_abstract(self):
        a = _make_article(tldr="Short TLDR.")
        text = _format_article_text(a)
        assert "TLDR: Short TLDR." in text

    def test_no_body_section_when_all_empty(self):
        a = _make_article()
        text = _format_article_text(a)
        assert "Full text" not in text
        assert "Abstract:" not in text
        assert "TLDR:" not in text
        # Header (title) still present.
        assert "Sample article" in text

    def test_full_text_emitted_verbatim(self):
        # Cap is enforced upstream in fulltext_assessor._extract_text (FULLTEXT_MAX_CHARS).
        # The formatter must NOT re-truncate — it should pass the excerpt through unchanged.
        body = "Z" * 5000
        a = _make_article(title="x", authors=[], year=None, fulltext_excerpt=body)
        text = _format_article_text(a)
        assert text.count("Z") == 5000


# ---------------------------------------------------------------------------
# fulltext_assessor.prepare — persistence to articles_screened.json
# ---------------------------------------------------------------------------

class TestPreparePersistsExcerpts:
    def test_extracts_and_writes_back_to_screened(self, tmp_path, monkeypatch):
        review, review_dir = _make_review(tmp_path, status=ReviewStatus.pdfs_downloaded)
        # Write articles_screened.json with one include article that has a matching PDF.
        article = _make_article(pmid="11111", screening_decision=ScreeningDecision.include)
        screened_path = review_dir / review.articles_screened_file
        screened_path.write_text(json.dumps([article.model_dump(mode="json")], indent=2))
        pdfs_dir = review_dir / "pdfs"
        pdfs_dir.mkdir()
        (pdfs_dir / "11111.pdf").write_bytes(b"%PDF-fake")

        monkeypatch.setattr(fa_module, "_extract_text_and_full",
                            lambda p: ("EXTRACTED PDF BODY", "EXTRACTED PDF BODY"))
        # prepare() prints to stdout — capture suppressed by pytest.
        fa_module.prepare(review_dir)

        rows = json.loads(screened_path.read_text())
        assert rows[0]["fulltext_excerpt"] == "EXTRACTED PDF BODY"

    def test_unmatched_article_keeps_none(self, tmp_path, monkeypatch):
        review, review_dir = _make_review(tmp_path, status=ReviewStatus.pdfs_downloaded)
        article = _make_article(pmid="99999", screening_decision=ScreeningDecision.include)
        screened_path = review_dir / review.articles_screened_file
        screened_path.write_text(json.dumps([article.model_dump(mode="json")], indent=2))
        (review_dir / "pdfs").mkdir()  # empty — no match

        monkeypatch.setattr(fa_module, "_extract_text_and_full",
                            lambda p: ("should not run", "should not run"))
        fa_module.prepare(review_dir)

        rows = json.loads(screened_path.read_text())
        assert rows[0].get("fulltext_excerpt") is None


# ---------------------------------------------------------------------------
# fulltext_assessor.persist_pdf_text — backfill
# ---------------------------------------------------------------------------

class TestPersistPdfTextBackfill:
    def test_backfills_excerpts_onto_included(self, tmp_path, monkeypatch):
        review, review_dir = _make_review(tmp_path, status=ReviewStatus.fulltext_assessed)
        included = [
            _make_article(pmid="11111").model_dump(mode="json"),
            _make_article(pmid="22222").model_dump(mode="json"),  # no PDF on disk
        ]
        (review_dir / review.articles_included_file).write_text(json.dumps(included, indent=2))
        # Provide a stub for articles_screened.json so persist_pdf_text doesn't fail on it.
        (review_dir / review.articles_screened_file).write_text(json.dumps([], indent=2))
        pdfs_dir = review_dir / "pdfs"
        pdfs_dir.mkdir()
        (pdfs_dir / "11111.pdf").write_bytes(b"%PDF-fake")

        monkeypatch.setattr(fa_module, "_extract_text", lambda p: "BACKFILLED PDF TEXT")
        fa_module.persist_pdf_text(review_dir)

        rows = json.loads((review_dir / review.articles_included_file).read_text())
        assert rows[0]["fulltext_excerpt"] == "BACKFILLED PDF TEXT"
        # Article without a matching PDF stays None.
        assert rows[1].get("fulltext_excerpt") is None

    def test_idempotent(self, tmp_path, monkeypatch):
        # Running backfill twice doesn't corrupt or duplicate anything.
        review, review_dir = _make_review(tmp_path, status=ReviewStatus.fulltext_assessed)
        included = [_make_article(pmid="11111").model_dump(mode="json")]
        (review_dir / review.articles_included_file).write_text(json.dumps(included, indent=2))
        (review_dir / review.articles_screened_file).write_text(json.dumps([], indent=2))
        (review_dir / "pdfs").mkdir()
        (review_dir / "pdfs" / "11111.pdf").write_bytes(b"%PDF-fake")

        monkeypatch.setattr(fa_module, "_extract_text", lambda p: "TEXT")
        fa_module.persist_pdf_text(review_dir)
        fa_module.persist_pdf_text(review_dir)

        rows = json.loads((review_dir / review.articles_included_file).read_text())
        assert len(rows) == 1
        assert rows[0]["fulltext_excerpt"] == "TEXT"


# ---------------------------------------------------------------------------
# data_extractor.save — extraction_source on articles
# ---------------------------------------------------------------------------

class TestSaveRecordsExtractionSource:
    def test_full_text_articles_get_full_text_source(self, tmp_path):
        review, review_dir = _make_review(tmp_path, status=ReviewStatus.fulltext_assessed)
        articles = [
            _make_article(fulltext_excerpt="pdf body", abstract="abs").model_dump(mode="json"),
            _make_article(abstract="abs only").model_dump(mode="json"),
            _make_article(tldr="tldr only").model_dump(mode="json"),
            _make_article().model_dump(mode="json"),
        ]
        (review_dir / review.articles_included_file).write_text(json.dumps(articles, indent=2))

        extractions = [
            {"orig_idx": i, "study_design": "RCT", "primary_outcomes": ["x"]} for i in range(4)
        ]
        de_module.save(review_dir, json.dumps({"extractions": extractions}), batch_size=50, batch_offset=0)

        rows = json.loads((review_dir / review.articles_included_file).read_text())
        assert rows[0]["extraction_source"] == "full_text"
        assert rows[1]["extraction_source"] == "abstract"
        assert rows[2]["extraction_source"] == "tldr"
        assert rows[3]["extraction_source"] == "none"


# ---------------------------------------------------------------------------
# evidence_table.csv — data_source column
# ---------------------------------------------------------------------------

class TestEvidenceTableDataSource:
    def test_csv_has_data_source_column(self, tmp_path):
        articles = [
            _make_article(fulltext_excerpt="pdf", extraction_source="full_text"),
            _make_article(abstract="abs", extraction_source="abstract"),
        ]
        out = tmp_path / "evidence.csv"
        _write_evidence_table(articles, out)

        with out.open() as f:
            rows = list(csv.DictReader(f))
        assert "data_source" in rows[0]
        assert rows[0]["data_source"] == "full_text"
        assert rows[1]["data_source"] == "abstract"

    def test_data_source_falls_back_when_field_absent(self, tmp_path):
        # Older reviews don't have extraction_source set; CSV should still derive a value.
        articles = [
            _make_article(fulltext_excerpt="pdf"),  # extraction_source=None
            _make_article(abstract="abs"),
            _make_article(),
        ]
        out = tmp_path / "evidence.csv"
        _write_evidence_table(articles, out)

        with out.open() as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["data_source"] == "full_text"
        assert rows[1]["data_source"] == "abstract"
        assert rows[2]["data_source"] == "none"


# ---------------------------------------------------------------------------
# synthesizer — Source line per article
# ---------------------------------------------------------------------------

class TestSynthesisContextSourceLine:
    def test_full_text_label(self):
        a = _make_article(fulltext_excerpt="pdf", extraction_source="full_text")
        ctx = _build_synthesis_context([a])
        assert "Source: full text" in ctx

    def test_abstract_only_label(self):
        a = _make_article(abstract="abs", extraction_source="abstract")
        ctx = _build_synthesis_context([a])
        assert "Source: abstract only" in ctx

    def test_tldr_label(self):
        a = _make_article(tldr="tldr", extraction_source="tldr")
        ctx = _build_synthesis_context([a])
        assert "Source: TLDR only" in ctx

    def test_unknown_when_field_missing(self):
        a = _make_article(extraction_source=None)
        ctx = _build_synthesis_context([a])
        assert "Source: unknown source" in ctx
