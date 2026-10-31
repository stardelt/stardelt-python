"""Tests for ``stardelt apply`` (stardelt.apply). No network: a fake session stands in."""

import io
import json

from stardelt import cli
from tests.test_api import CANARY, FakeResponse, FakeSession

ENV = {"STARDELT_NOVA_URL": "https://nova.example", "STARDELT_TOKEN": CANARY}
SPEC = {"name": "daily", "ownerGroup": "etl", "sql": "select 1", "schedule": {"cron": "0 3 * * *"}}
EXISTING = {"pipelines": [{"name": "daily", "namespace": "stardelt", "ownerGroup": "etl"}]}


def run(argv, *responses, stdin=""):
    out, err = io.StringIO(), io.StringIO()
    session = FakeSession(*responses)
    code = cli.main(argv, environ=ENV, stdout=out, stderr=err, session=session, stdin=io.StringIO(stdin))
    return code, out.getvalue(), err.getvalue(), session


def write(tmp_path, spec):
    path = tmp_path / "pipeline.json"
    path.write_text(spec if isinstance(spec, str) else json.dumps(spec))
    return str(path)


def test_a_new_name_is_created_with_the_file_as_the_body(tmp_path):
    code, out, err, session = run(
        ["apply", "-f", write(tmp_path, SPEC)], FakeResponse(payload={"pipelines": []}), FakeResponse(201, {"name": "daily"})
    )
    assert (code, json.loads(out), err) == (0, {"action": "created", "name": "daily"}, "")
    assert [(m, u) for m, u, _ in session.calls] == [
        ("GET", "https://nova.example/api/pipelines"),
        ("POST", "https://nova.example/api/pipelines"),
    ]
    assert session.calls[1][2]["json"] == SPEC


def test_an_existing_name_is_updated_without_the_name_and_the_group(tmp_path):
    code, out, _, session = run(
        ["apply", "-f", write(tmp_path, SPEC)], FakeResponse(payload=EXISTING), FakeResponse(payload={"name": "daily"})
    )
    assert (code, json.loads(out)) == (0, {"action": "updated", "name": "daily"})
    method, url, kwargs = session.calls[1]
    assert (method, url) == ("PUT", "https://nova.example/api/pipelines/stardelt/daily")
    assert kwargs["json"] == {"sql": "select 1", "schedule": {"cron": "0 3 * * *"}}


def test_only_the_pipeline_of_that_name_counts_when_others_exist(tmp_path):
    others = {"pipelines": [{"name": "hourly", "namespace": "other", "ownerGroup": "etl"}]}
    code, out, _, session = run(
        ["apply", "-f", write(tmp_path, SPEC)], FakeResponse(payload=others), FakeResponse(201, {"name": "daily"})
    )
    assert (code, json.loads(out)["action"], session.calls[1][0]) == (0, "created", "POST")
    both = {"pipelines": others["pipelines"] + EXISTING["pipelines"]}
    _, _, _, session = run(["apply", "-f", write(tmp_path, SPEC)], FakeResponse(payload=both), FakeResponse(payload={}))
    assert session.calls[1][1] == "https://nova.example/api/pipelines/stardelt/daily"


def test_dry_run_reads_but_writes_nothing(tmp_path):
    code, out, _, session = run(["apply", "-f", write(tmp_path, SPEC), "--dry-run"], FakeResponse(payload=EXISTING))
    assert (code, json.loads(out), len(session.calls)) == (0, {"action": "would-update", "name": "daily"}, 1)
    code, out, _, session = run(
        ["apply", "-f", write(tmp_path, SPEC), "--dry-run"], FakeResponse(payload={"pipelines": []})
    )
    assert (code, json.loads(out), len(session.calls)) == (0, {"action": "would-create", "name": "daily"}, 1)


def test_another_owner_group_is_refused_before_anything_is_written(tmp_path):
    other = dict(SPEC, ownerGroup="finance")
    code, out, err, session = run(["apply", "-f", write(tmp_path, other)], FakeResponse(payload=EXISTING))
    assert (code, out, len(session.calls)) == (2, "", 1)
    assert "belongs to the group etl" in err and "ownerGroup cannot change" in err


def test_a_file_that_is_not_a_pipeline_is_a_usage_error_without_a_request(tmp_path):
    for bad in ("{oops", "[1]", json.dumps({"sql": "select 1"}), json.dumps({"name": ""})):
        code, out, err, session = run(["apply", "-f", write(tmp_path, bad)])
        assert (code, out, session.calls) == (2, "", []) and err.startswith("stardelt: ")
    code, _, err, session = run(["apply", "-f", str(tmp_path / "missing.json")])
    assert (code, session.calls) == (2, []) and "missing.json" in err


def test_a_dash_reads_the_pipeline_from_stdin():
    code, out, _, session = run(
        ["apply", "-f", "-"], FakeResponse(payload={"pipelines": []}), FakeResponse(201, {}), stdin=json.dumps(SPEC)
    )
    assert (code, json.loads(out)["action"], session.calls[1][2]["json"]) == (0, "created", SPEC)


YAML = """\
name: daily
ownerGroup: etl
sql: select 1
schedule:
  cron: "0 3 * * *"
"""


def test_a_yaml_file_is_applied_like_the_json_one(tmp_path):
    path = tmp_path / "pipeline.yaml"
    path.write_text(YAML)
    code, out, _, session = run(
        ["apply", "-f", str(path)], FakeResponse(payload={"pipelines": []}), FakeResponse(201, {"name": "daily"})
    )
    assert (code, json.loads(out)["action"]) == (0, "created")
    assert session.calls[1][2]["json"] == SPEC


def test_a_yaml_value_json_cannot_carry_is_a_usage_error(tmp_path):
    path = tmp_path / "pipeline.yaml"
    path.write_text(YAML + "startAfter: 2026-10-31\n")
    code, out, err, session = run(["apply", "-f", str(path)])
    assert (code, out, session.calls) == (2, "", []) and "put it in quotes" in err


def test_a_file_that_is_neither_json_nor_yaml_says_so(tmp_path):
    code, _, err, session = run(["apply", "-f", write(tmp_path, "{oops: [")])
    assert (code, session.calls) == (2, []) and "neither JSON nor YAML" in err


def test_the_flag_file_is_required():
    code, _, err, session = run(["apply"])
    assert (code, session.calls) == (2, []) and "--file" in err


def test_nova_refusing_the_write_is_an_error_with_its_sentence(tmp_path):
    code, out, err, _ = run(
        ["apply", "-f", write(tmp_path, SPEC)],
        FakeResponse(payload={"pipelines": []}),
        FakeResponse(403, {"error": "you are not a member of the group etl"}),
    )
    assert (code, out) == (1, "") and "you are not a member of the group etl (HTTP 403)" in err
