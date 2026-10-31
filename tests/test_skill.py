"""The agent skill (stardelt.skill): the text ships, installs, and names only tools that exist."""

import io
import re

from stardelt import cli, mcp, skill


def run(argv, environ=None):
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(argv, environ={} if environ is None else environ, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def test_the_command_prints_the_skill_without_a_host_or_a_sign_in():
    code, out, err = run(["skill"])
    assert (code, err) == (0, "")
    assert out == skill.text()
    assert out.startswith("---\nname: stardelt\n") and "\nwhen_to_use: " in out


def test_every_tool_the_skill_names_exists_on_the_server():
    names = set(re.findall(r"`([a-z]+(?:_[a-z]+)+)`", skill.text()))
    tools = set(mcp.DEFAULT_TOOLS) | set(mcp.SQL_TOOLS)
    assert names, "the skill names no tool"
    assert names <= tools, sorted(names - tools)


def test_write_installs_the_skill_where_an_assistant_looks_for_it(tmp_path):
    code, out, err = run(["skill", "--write", str(tmp_path / ".claude" / "skills")])
    target = tmp_path / ".claude" / "skills" / "stardelt" / "SKILL.md"
    assert (code, out) == (0, "") and str(target) in err
    assert target.read_text(encoding="utf-8") == skill.text()


def test_write_into_a_place_that_cannot_be_written_is_an_error_with_a_sentence(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    code, out, err = run(["skill", "--write", str(blocker)])
    assert (code, out) == (1, "") and err.startswith("stardelt: ")
