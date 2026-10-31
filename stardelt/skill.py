"""The agent skill: ``stardelt skill`` prints ``SKILL.md``; ``stardelt skill --write DIR`` installs it as
``DIR/stardelt/SKILL.md`` (for example ``.claude/skills``). The text is for an AI assistant that uses the stardelt
MCP tools or the command line: how to find a table, how to query, what never to do. It names only tools that exist
(``tests/test_skill.py`` checks every name against the server's tool list)."""

from pathlib import Path

PATH = Path(__file__).with_name("SKILL.md")


def text():
    return PATH.read_text(encoding="utf-8")


def write(directory):
    """Write the skill as ``directory/stardelt/SKILL.md`` and return that path."""
    target = Path(directory) / "stardelt" / "SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text(), encoding="utf-8")
    return target
