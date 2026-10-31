"""Tests for the resource-first commands (stardelt.shortcuts). No network: a fake session stands in."""

import io
import json

import pytest

from stardelt import api, cli, shortcuts
from tests.test_api import FakeResponse, FakeSession
from tests.test_cli import ENV

OPS = api.operations()


def test_a_verb_and_its_path_values_become_the_api_command():
    assert shortcuts.expand(["pipelines", "get", "etl", "daily"], OPS) == [
        "api", "pipelines_get", "-p", "namespace=etl", "-p", "name=daily",
    ]


def test_options_after_the_values_are_the_options_of_api_and_host_stays_in_front():
    argv = ["--host", "https://n", "pipelines", "list", "--format", "table", "-q", "limit=5"]
    assert shortcuts.expand(argv, OPS) == [
        "--host", "https://n", "api", "pipelines_list", "--format", "table", "-q", "limit=5",
    ]


def test_a_verb_of_several_words_joins_them_with_a_dash():
    assert shortcuts.expand(["pipelines", "run-get", "etl", "daily", "r1"], OPS)[:2] == ["api", "pipelines_run_get"]


def test_a_resource_with_one_operation_and_no_verb_is_the_operation():
    assert shortcuts.expand(["me"], OPS) == ["api", "me"]
    assert shortcuts.expand(["health", "--format", "json"], OPS) == ["api", "health", "--format", "json"]


def test_every_operation_id_can_be_reached_by_its_words():
    for operation_id, op in OPS.items():
        resource, verb = shortcuts._split(operation_id)
        argv = [resource] + ([verb] if verb else []) + [f"v{i}" for i, _ in enumerate(op["path_params"])]
        expanded = shortcuts.expand(argv, OPS)
        assert expanded[:2] == ["api", operation_id], operation_id
        assert expanded.count("-p") == len(op["path_params"]), operation_id


def test_commands_options_and_unknown_words_are_left_alone():
    untouched = [["whoami"], ["sql", "select 1"], ["api", "--list"], ["mcp", "serve"]]
    untouched += [["--help"], ["nothing", "here"], []]
    for argv in untouched:
        assert shortcuts.expand(argv, OPS) == argv


def test_a_resource_named_like_a_command_never_hides_the_command():
    assert shortcuts.expand(["sql", "run"], {"sql_run": {"path_params": []}}) == ["sql", "run"]


def test_a_missing_or_unknown_verb_lists_the_verbs_with_their_values():
    for argv in (["pipelines"], ["pipelines", "nope"]):
        with pytest.raises(ValueError, match=r"pipelines: pick one of: .*get NAMESPACE NAME"):
            shortcuts.expand(argv, OPS)


def test_a_missing_path_value_names_what_is_needed():
    with pytest.raises(ValueError, match=r"pipelines get needs NAMESPACE NAME"):
        shortcuts.expand(["pipelines", "get", "etl"], OPS)
    with pytest.raises(ValueError, match=r"pipelines get needs NAMESPACE NAME"):
        shortcuts.expand(["pipelines", "get", "etl", "--format", "table"], OPS)


def test_the_help_lists_every_resource():
    text = shortcuts.summary(OPS)
    assert "  pipelines: check, create" in text and "  me" in text.splitlines()
    assert len(text.splitlines()) == len(shortcuts.resources(OPS))


def run(argv, *responses):
    out, err = io.StringIO(), io.StringIO()
    session = FakeSession(*responses)
    code = cli.main(argv, environ=ENV, stdout=out, stderr=err, session=session)
    return code, out.getvalue(), err.getvalue(), session


def test_the_command_calls_the_operation_with_the_path_filled_in():
    code, out, err, session = run(["pipelines", "get", "etl", "daily"], FakeResponse(payload={"name": "daily"}))
    assert (code, json.loads(out), err) == (0, {"name": "daily"}, "")
    assert session.calls[0][:2] == ("GET", "https://nova.example/api/pipelines/etl/daily")


def test_a_usage_mistake_exits_2_and_says_what_is_missing_on_stderr():
    code, out, err, session = run(["pipelines", "get", "etl"])
    assert (code, out, session.calls) == (2, "", [])
    assert err == "stardelt: pipelines get needs NAMESPACE NAME\n"
