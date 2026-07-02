"""Phase C0 tests: _base.py utilities and no-anthropic-import guard."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.skills._base import die, error_output, parse_save_input, prepare_output


class TestPrepareOutput:
    def test_returns_valid_json(self):
        data = {"review_type": "systematic", "rationale": "Rigorous question."}
        result = prepare_output(data)
        parsed = json.loads(result)
        assert parsed == data

    def test_compact_separators(self):
        result = prepare_output({"a": 1})
        assert " " not in result  # no spaces — compact JSON

    def test_non_ascii_preserved(self):
        result = prepare_output({"title": "Hépatite"})
        parsed = json.loads(result)
        assert parsed["title"] == "Hépatite"

    def test_nested_dict(self):
        data = {"pico": {"P": "CHF patients", "I": "care transitions", "C": "usual care", "O": "readmission"}}
        result = prepare_output(data)
        assert json.loads(result) == data

    def test_list_value(self):
        data = {"articles": [{"id": "1"}, {"id": "2"}]}
        result = prepare_output(data)
        assert json.loads(result)["articles"][1]["id"] == "2"


class TestErrorOutput:
    def test_returns_error_json(self):
        result = error_output("Something went wrong")
        parsed = json.loads(result)
        assert parsed == {"error": "Something went wrong"}

    def test_empty_message(self):
        result = error_output("")
        assert json.loads(result)["error"] == ""


class TestParseSaveInput:
    def test_valid_json_object(self):
        result = parse_save_input('{"review_type": "systematic"}')
        assert result["review_type"] == "systematic"

    def test_invalid_json_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid JSON"):
            parse_save_input("not json at all")

    def test_json_array_raises_value_error(self):
        with pytest.raises(ValueError, match="Expected a JSON object"):
            parse_save_input("[1, 2, 3]")

    def test_json_string_raises_value_error(self):
        with pytest.raises(ValueError, match="Expected a JSON object"):
            parse_save_input('"just a string"')

    def test_nested_object_preserved(self):
        payload = '{"decisions": [{"id": "abc", "include": true, "reason": "matches PICO"}]}'
        result = parse_save_input(payload)
        assert result["decisions"][0]["include"] is True

    def test_unicode_preserved(self):
        result = parse_save_input('{"title": "Hépatite"}')
        assert result["title"] == "Hépatite"


class TestNoAnthropicImport:
    """Guard: src/ must contain no anthropic SDK imports.

    This is xfail in C0 because individual skill files are converted in C1–C5.
    The xfail strict=True marker in test_skills_phase_c5.py will enforce this
    as a hard failure once all conversions are complete.
    """

    def _src_python_files(self) -> list[Path]:
        src = PROJECT_ROOT / "src"
        return list(src.rglob("*.py"))

    def test_no_import_anthropic(self):
        violations = []
        for path in self._src_python_files():
            text = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("import anthropic") or stripped.startswith("from anthropic"):
                    violations.append(f"{path.relative_to(PROJECT_ROOT)}:{lineno}: {stripped}")
        assert violations == [], (
            "anthropic SDK imports found in src/:\n" + "\n".join(violations)
        )

    def test_src_files_exist(self):
        files = self._src_python_files()
        assert len(files) > 10, "Expected many Python files in src/"


class TestNoAnthropicInRequirements:
    def test_anthropic_not_in_requirements(self):
        req = (PROJECT_ROOT / "requirements.txt").read_text()
        assert "anthropic" not in req, "anthropic must be removed from requirements.txt"

    def test_requirements_still_has_core_deps(self):
        req = (PROJECT_ROOT / "requirements.txt").read_text()
        for dep in ["pydantic", "httpx", "pyyaml"]:
            assert dep in req, f"{dep} missing from requirements.txt"
