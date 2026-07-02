"""Smoke tests: each skills/*.md file must have valid YAML frontmatter with name and description."""
import re
from pathlib import Path

import pytest
import yaml

SKILLS_DIR = Path(__file__).parent.parent / "skills"
SKILL_FILES = list(SKILLS_DIR.glob("*.md"))


def _parse_frontmatter(skill_file: Path) -> dict:
    content = skill_file.read_text()
    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        pytest.fail(f"{skill_file.name}: could not parse YAML frontmatter block")
    meta = yaml.safe_load(match.group(1))
    if not isinstance(meta, dict):
        pytest.fail(f"{skill_file.name}: frontmatter is not a YAML mapping")
    return meta


@pytest.mark.parametrize("skill_file", SKILL_FILES, ids=[f.name for f in SKILL_FILES])
def test_skill_file_frontmatter_parses(skill_file: Path) -> None:
    _parse_frontmatter(skill_file)


@pytest.mark.parametrize("skill_file", SKILL_FILES, ids=[f.name for f in SKILL_FILES])
def test_skill_file_has_name_and_description(skill_file: Path) -> None:
    meta = _parse_frontmatter(skill_file)
    assert "name" in meta, f"{skill_file.name}: frontmatter missing 'name' key"
    assert "description" in meta, f"{skill_file.name}: frontmatter missing 'description' key"


@pytest.mark.parametrize("skill_file", SKILL_FILES, ids=[f.name for f in SKILL_FILES])
def test_skill_file_name_is_valid_slug(skill_file: Path) -> None:
    meta = _parse_frontmatter(skill_file)
    name = meta.get("name", "")
    assert re.match(r"^[a-z0-9-]+$", name), (
        f"{skill_file.name}: 'name' must be a lowercase slug (letters, digits, hyphens); got {name!r}"
    )
