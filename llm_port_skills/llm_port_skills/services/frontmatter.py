"""Parse YAML frontmatter from a skill markdown document."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml


@dataclass
class ParsedSkill:
    """Result of parsing a skill markdown document."""

    frontmatter: dict[str, Any] = field(default_factory=dict)
    body: str = ""
    raw_frontmatter: str = ""


def parse_skill_document(content: str) -> ParsedSkill:
    """Parse a skill document with YAML frontmatter + markdown body.

    Expected format:
        ---
        name: My Skill
        tags: [finance, analysis]
        ---
        ## Goal
        Extract major trends...
    """
    content = content.strip()
    if not content.startswith("---"):
        return ParsedSkill(body=content)

    # Find the closing ---
    end_idx = content.find("---", 3)
    if end_idx == -1:
        return ParsedSkill(body=content)

    raw_fm = content[3:end_idx].strip()
    body = content[end_idx + 3 :].strip()

    try:
        frontmatter = yaml.safe_load(raw_fm)
        if not isinstance(frontmatter, dict):
            frontmatter = {}
    except yaml.YAMLError:
        frontmatter = {}

    return ParsedSkill(
        frontmatter=frontmatter,
        body=body,
        raw_frontmatter=raw_fm,
    )


def compose_skill_document(frontmatter: dict[str, Any], body: str) -> str:
    """Compose a skill document from frontmatter dict + markdown body."""
    fm_yaml = yaml.dump(
        frontmatter,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    ).strip()
    return f"---\n{fm_yaml}\n---\n\n{body}"


def extract_metadata_from_frontmatter(
    fm: dict[str, Any],
) -> dict[str, Any]:
    """Extract structured metadata fields from frontmatter dict.

    Returns a dict with keys matching SkillModel columns.
    Unknown keys are ignored.
    """
    known_keys = {
        "name",
        "description",
        "scope",
        "status",
        "enabled",
        "priority",
        "tags",
        "allowed_tools",
        "preferred_tools",
        "forbidden_tools",
        "knowledge_sources",
        "trigger_rules",
    }
    return {k: v for k, v in fm.items() if k in known_keys}
