from src.models.review import Review, ReviewStatus


def test_review_defaults_packaged_fields_to_unpacked():
    r = Review(review_id="x", raw_question="q", status=ReviewStatus.complete)
    assert r.packaged is False
    assert r.packaged_at is None
    assert r.packaged_artifacts == []
    assert r.packaged_pandoc_version is None
    assert r.packaged_audit_summary == {}


def test_review_round_trips_packaged_fields():
    r = Review(
        review_id="x",
        raw_question="q",
        status=ReviewStatus.complete,
        packaged=True,
        packaged_at=None,
        packaged_artifacts=["package.md", "x_report.pdf"],
        packaged_pandoc_version="3.9.0.2",
        packaged_audit_summary={"cards_emitted": 3},
    )
    payload = r.model_dump()
    restored = Review.model_validate(payload)
    assert restored.packaged is True
    assert restored.packaged_artifacts == ["package.md", "x_report.pdf"]
    assert restored.packaged_audit_summary == {"cards_emitted": 3}


def test_existing_state_json_loads_without_packaged_fields():
    """Backwards compat: completed reviews written before packaging exists must still load."""
    legacy = {"review_id": "old", "raw_question": "q", "status": "complete"}
    r = Review.model_validate(legacy)
    assert r.packaged is False
