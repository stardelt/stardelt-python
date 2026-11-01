"""``stardelt bundle plan --diff``: what an update changes, field by field, on standard error. No network."""

import io
import json

from stardelt import cli
from tests.test_api import CANARY, FakeResponse, FakeSession
from tests.test_bundle import make

ENV = {"STARDELT_NOVA_URL": "https://nova.example", "STARDELT_TOKEN": CANARY}
ME = FakeResponse(payload={"user": "alice"})
CREATE = {"ok": True, "action": "create", "errors": [], "changes": [], "nextRuns": []}
LISTING = {"pipelines": [{"name": "load", "namespace": "stardelt", "ownerGroup": "sales"}]}


def update(*changes):
    return {"ok": True, "action": "update", "errors": [], "changes": list(changes), "nextRuns": []}


def deployed(**fields):
    return FakeResponse(payload={"document": {"name": "load", "ownerGroup": "sales", "sql": "select 1", **fields}, "managedBy": "nova", "editable": True})


def run(argv, *responses):
    out, err = io.StringIO(), io.StringIO()
    session = FakeSession(*responses)
    code = cli.main(argv, environ=ENV, stdout=out, stderr=err, session=session, stdin=io.StringIO(""))
    return code, out.getvalue(), err.getvalue(), session


def methods(session):
    return [method for method, _, _ in session.calls]


def test_a_changed_statement_is_shown_as_a_unified_diff_and_the_json_lines_keep_their_shape(tmp_path):
    make(tmp_path)
    code, out, err, session = run(
        ["bundle", "plan", str(tmp_path), "-t", "prod", "--diff"],
        ME, FakeResponse(payload=update("statement", "deployment")), FakeResponse(payload=CREATE),
        FakeResponse(payload=LISTING), deployed(sql="select 0"),
    )
    assert code == 0
    assert [json.loads(line) for line in out.splitlines()] == [
        {"action": "would-update", "name": "load", "changes": ["statement", "deployment"]},
        {"action": "would-create", "name": "nightly"},
    ]
    assert "--- load sql (deployed)\n+++ load sql (bundle)\n@@ -1 +1 @@\n-select 0\n+select 1\n" in err
    assert err.count("---") == 1, "the stamp (deployment) and the created pipeline have no diff"
    assert [url.removeprefix("https://nova.example") for _, url, _ in session.calls][-2:] == [
        "/api/pipelines",
        "/api/pipelines/stardelt/load/spec",
    ]


def test_a_changed_schedule_is_shown_as_yaml(tmp_path):
    make(tmp_path)
    code, _, err, _ = run(
        ["bundle", "plan", str(tmp_path), "-t", "prod", "--diff"],
        ME, FakeResponse(payload=update("schedule")), FakeResponse(payload=CREATE),
        FakeResponse(payload=LISTING), deployed(schedule={"cron": "0 4 * * *", "timezone": "Europe/Berlin"}),
    )
    assert code == 0
    assert "--- load schedule (deployed)\n+++ load schedule (bundle)" in err
    assert "-cron: 0 4 * * *\n+cron: 0 3 * * *\n" in err and " timezone: Europe/Berlin" in err


def test_without_the_flag_nothing_more_is_asked_of_nova(tmp_path):
    make(tmp_path)
    code, out, err, session = run(
        ["bundle", "plan", str(tmp_path), "-t", "prod"], ME, FakeResponse(payload=update("statement")), FakeResponse(payload=CREATE)
    )
    assert code == 0 and err == "" and "diff" not in out
    assert methods(session) == ["GET", "POST", "POST"]


def test_a_change_of_the_stamp_alone_asks_for_no_document_and_shows_no_diff(tmp_path):
    make(tmp_path)
    code, _, err, session = run(
        ["bundle", "plan", str(tmp_path), "-t", "prod", "--diff"],
        ME, FakeResponse(payload=update("deployment")), FakeResponse(payload=CREATE),
    )
    assert (code, err) == (0, "")
    assert methods(session) == ["GET", "POST", "POST"]


def test_a_deployed_document_that_cannot_be_read_is_one_sentence_and_the_plan_still_succeeds(tmp_path):
    make(tmp_path)
    code, out, err, _ = run(
        ["bundle", "plan", str(tmp_path), "-t", "prod", "--diff"],
        ME, FakeResponse(payload=update("statement")), FakeResponse(payload=CREATE),
        FakeResponse(payload=LISTING), FakeResponse(status=403, payload={"error": "this pipeline is not yours"}),
    )
    assert code == 0 and len(out.splitlines()) == 2
    assert err == "load: the difference is not available (this pipeline is not yours (HTTP 403))\n"


def test_deploy_shows_the_difference_and_writes_as_before(tmp_path):
    make(tmp_path)
    code, out, err, session = run(
        ["bundle", "deploy", str(tmp_path), "-t", "prod", "--diff"],
        ME, FakeResponse(payload=update("statement")), FakeResponse(payload=CREATE),
        FakeResponse(payload=LISTING), deployed(sql="select 0"),
        FakeResponse(payload=LISTING), FakeResponse(payload={"name": "load"}),
        FakeResponse(payload={"pipelines": []}), FakeResponse(201, {"name": "nightly"}),
    )
    assert code == 0
    assert [json.loads(line)["action"] for line in out.splitlines()] == ["updated", "created"]
    assert "-select 0\n+select 1\n" in err
    assert methods(session).count("PUT") == 1 and methods(session).count("POST") == 3
