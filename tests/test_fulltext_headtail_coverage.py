"""#63 head+tail extraction and #62 title-coverage confidence heuristic."""
from __future__ import annotations

from src.skills.fulltext_assessor import (
    FULLTEXT_HEAD_CHARS,
    FULLTEXT_MAX_CHARS,
    FULLTEXT_TAIL_CHARS,
    _head_tail_excerpt,
    _pdf_match_confidence,
    _title_coverage_in_pdf,
)


class TestHeadTailExcerpt:
    def test_captures_both_ends_drops_middle(self):
        # A long article: Funding/COI/Limitations live in the TAIL, which the old first-N cap lost.
        head = "HEADSTART " + "a " * FULLTEXT_HEAD_CHARS
        middle = "m" * 40000
        tail = "z " * FULLTEXT_TAIL_CHARS + " FUNDING_DISCLOSURE_TAILEND"
        full = head + middle + tail
        ex = _head_tail_excerpt(full)
        assert "HEADSTART" in ex
        assert "FUNDING_DISCLOSURE_TAILEND" in ex
        assert "m" * 40000 not in ex  # middle dropped
        assert "truncated" in ex.lower()
        assert len(ex) < len(full)

    def test_short_text_returned_whole_no_marker(self):
        full = "A short article body well under the cap."
        assert _head_tail_excerpt(full) == full  # no marker, no duplicated tail

    def test_cap_is_head_plus_tail(self):
        assert FULLTEXT_MAX_CHARS == FULLTEXT_HEAD_CHARS + FULLTEXT_TAIL_CHARS


class TestTitleCoverage:
    def test_full_coverage_for_short_title_diluted_by_metadata(self):
        # #62 case (a)/(b): short title, all words present but buried in an author/affiliation
        # block. Old Jaccard scored this < 0.10 (false flag); coverage is 1.0.
        title = "Financial Implications of Short Stay Pediatric Hospitalizations"
        text = (
            title + " " + "Author One, Author Two, Department of Pediatrics, "
            "University Medical Center, " * 40
        )
        assert _title_coverage_in_pdf(title, text) == 1.0

    def test_zero_coverage_for_off_topic(self):
        title = "Pediatric subspecialty telemedicine consult outcomes"
        text = "Coeliac disease screening for first-degree relatives of confirmed cases " * 30
        assert _title_coverage_in_pdf(title, text) < 0.5

    def test_empty_inputs_zero(self):
        assert _title_coverage_in_pdf("", "anything") == 0.0
        assert _title_coverage_in_pdf("anything else", "") == 0.0


class TestPdfMatchConfidence:
    def test_flags_short_extraction(self):
        low, reason = _pdf_match_confidence("Pediatric telemedicine outcomes", "tiny stub")
        assert low is True
        assert "length" in reason.lower()

    def test_flags_genuine_wrong_pdf(self):
        # #48 protection must survive: a truly off-topic PDF is still flagged.
        title = "Pediatric subspecialty telemedicine consult outcomes in community hospitals"
        text = "Coeliac disease screening recommendations for relatives " * 50
        low, reason = _pdf_match_confidence(title, text)
        assert low is True
        assert "coverage" in reason.lower()

    def test_not_flagged_when_title_words_present_later_in_full_text(self):
        # #63 coupling: the target title appears past the first 1000 chars (bundled/late-page PDF).
        # Coverage over the FULL text still finds the words → not flagged.
        title = "Pediatric Hospitalization Trends at Children and General Hospitals"
        preamble = "Unrelated research letter about labor board physician unionization. " * 20
        target = ("Pediatric hospitalization trends were examined across children and general "
                  "hospitals over a decade using national data. ") * 5
        low, reason = _pdf_match_confidence(title, preamble + target)
        assert low is False
        assert reason == ""
