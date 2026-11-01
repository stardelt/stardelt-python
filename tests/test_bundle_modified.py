"""A pipeline someone changed after its last deploy: ``modified`` in the answer and one warning on standard error."""

import io
import json

from stardelt import cli
from tests.test_api import CANARY, FakeResponse, FakeSession
from tests.test_bundle import make

ENV = {"STARDELT_NOVA_URL": "https://nova.example", "STARDELT_TOKEN": CANARY}
ME = FakeResponse(payload={"user": "alice"})
UPDATE = {"ok": True, "action": "update", "errors": [], "changes": ["statement"], "nextRuns": []}
BY_HAND = dict(UPDATE, modified=True)
WARNING = "stardelt: warning: nightly was changed after its last deploy; deploying overwrites that change\n"
EXISTING = {"pipelines": [{"name": "load", "namespace": "stardelt", "ownerGroup": "sales"}, {"name": "nightly", "namespace": "stardelt", "ownerGroup": "sales"}]}


def run(argv, *responses):
    out, err = io.StringIO(), io.StringIO()
    session = FakeSession(*responses)
    code = cli.main(argv, environ=ENV, stdout=out, stderr=err, session=session, stdin=io.StringIO(""))
    return code, out.getvalue(), err.getvalue(), session


def calls(session):
    return [(method, url.removeprefix("https://nova.example")) for method, url, _ in session.calls]


def test_apply_dry_run_carries_modified_and_warns_only_then(tmp_path):
    path = tmp_path / "nightly.yaml"
    path.write_text("name: nightly\nownerGroup: sales\nsql: select 1\n")
    code, out, err, _ = run(["apply", "-f", str(path), "--dry-run"], FakeResponse(payload=BY_HAND))
    assert code == 0
    assert json.loads(out) == {"action": "would-update", "name": "nightly", "changes": ["statement"], "modified": True}
    assert err == WARNING
    code, out, err, _ = run(["apply", "-f", str(path), "--dry-run"], FakeResponse(payload=UPDATE))
    assert (code, err) == (0, "") and "modified" not in out


def test_plan_warns_once_for_each_pipeline_changed_by_hand_and_writes_nothing(tmp_path):
    make(tmp_path)
    code, out, err, session = run(
        ["bundle", "plan", str(tmp_path), "-t", "prod"], ME, FakeResponse(payload=UPDATE), FakeResponse(payload=BY_HAND)
    )
    assert code == 0
    assert [json.loads(line) for line in out.splitlines()] == [
        {"action": "would-update", "name": "load", "changes": ["statement"]},
        {"action": "would-update", "name": "nightly", "changes": ["statement"], "modified": True},
    ]
    assert err == WARNING
    assert [method for method, _ in calls(session)] == ["GET", "POST", "POST"], "a plan writes nothing"


def test_deploy_still_overwrites_and_keeps_the_fact_in_its_answer(tmp_path):
    make(tmp_path)
    code, out, err, session = run(
        ["bundle", "deploy", str(tmp_path), "-t", "prod"],
        ME, FakeResponse(payload=UPDATE), FakeResponse(payload=BY_HAND),
        FakeResponse(payload=EXISTING), FakeResponse(payload={"name": "load"}),
        FakeResponse(payload=EXISTING), FakeResponse(payload={"name": "nightly"}),
    )
    assert code == 0
    assert [json.loads(line) for line in out.splitlines()] == [
        {"action": "updated", "name": "load"},
        {"action": "updated", "name": "nightly", "modified": True},
    ]
    assert err == WARNING
    assert [(m, u) for m, u in calls(session) if m == "PUT"] == [
        ("PUT", "/api/pipelines/stardelt/load"),
        ("PUT", "/api/pipelines/stardelt/nightly"),
    ]
