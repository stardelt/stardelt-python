"""A target that says ``drift: block`` refuses to overwrite a pipeline someone changed after its last deploy. No network."""

import io
import json

from stardelt import bundle, cli
from tests.test_api import CANARY, FakeResponse, FakeSession
from tests.test_bundle import MANIFEST, make

ENV = {"STARDELT_NOVA_URL": "https://nova.example", "STARDELT_TOKEN": CANARY}
ME = FakeResponse(payload={"user": "alice"})
UPDATE = {"ok": True, "action": "update", "errors": [], "changes": ["statement"], "nextRuns": []}
BY_HAND = dict(UPDATE, modified=True)
BLOCK = MANIFEST.replace("runAs: pipeline-sales}", "runAs: pipeline-sales, drift: block}")
SENTENCE = "nightly was changed after its last deploy and this target says drift: block; look at the change, then pass --accept-drift to overwrite it"
EXISTING = {
    "pipelines": [
        {"name": "load", "namespace": "stardelt", "ownerGroup": "sales"},
        {"name": "nightly", "namespace": "stardelt", "ownerGroup": "sales"},
    ]
}


def run(argv, *responses):
    out, err = io.StringIO(), io.StringIO()
    session = FakeSession(*responses)
    code = cli.main(argv, environ=ENV, stdout=out, stderr=err, session=session, stdin=io.StringIO(""))
    return code, out.getvalue(), err.getvalue(), session


def methods(session):
    return [method for method, _, _ in session.calls]


def bundle_in(tmp_path, name, manifest):
    root = tmp_path / name
    root.mkdir()
    return make(root, manifest=manifest)


def test_the_drift_setting_is_checked_offline_and_defaults_to_warn(tmp_path):
    assert bundle.validate(bundle_in(tmp_path, "block", BLOCK))[0] == []
    assert bundle.validate(bundle_in(tmp_path, "default", MANIFEST))[0] == []
    strict = MANIFEST.replace("runAs: pipeline-sales}", "runAs: pipeline-sales, drift: strict}")
    assert bundle.validate(bundle_in(tmp_path, "strict", strict))[0] == [("targets.prod", "drift must be one of warn, block")]


def test_plan_of_a_blocking_target_lists_everything_and_fails_for_the_pipeline_changed_by_hand(tmp_path):
    make(tmp_path, manifest=BLOCK)
    code, out, err, session = run(
        ["bundle", "plan", str(tmp_path), "-t", "prod"], ME, FakeResponse(payload=UPDATE), FakeResponse(payload=BY_HAND)
    )
    assert code == 2
    assert [json.loads(line)["name"] for line in out.splitlines()] == ["load", "nightly"], "what was planned is still listed"
    assert f"stardelt: prod: {SENTENCE}\n" in err
    assert methods(session) == ["GET", "POST", "POST"], "a plan writes nothing"


def test_accept_drift_lets_the_plan_pass_and_the_warning_stays(tmp_path):
    make(tmp_path, manifest=BLOCK)
    code, _, err, _ = run(
        ["bundle", "plan", str(tmp_path), "-t", "prod", "--accept-drift"], ME, FakeResponse(payload=UPDATE), FakeResponse(payload=BY_HAND)
    )
    assert code == 0
    assert err == "stardelt: warning: nightly was changed after its last deploy; deploying overwrites that change\n"


def test_a_blocking_deploy_writes_nothing_and_with_accept_drift_it_writes(tmp_path):
    make(tmp_path, manifest=BLOCK)
    code, _, err, session = run(
        ["bundle", "deploy", str(tmp_path), "-t", "prod"], ME, FakeResponse(payload=UPDATE), FakeResponse(payload=BY_HAND)
    )
    assert code == 2 and SENTENCE in err
    assert methods(session) == ["GET", "POST", "POST"], "no listing, no write"
    code, out, _, session = run(
        ["bundle", "deploy", str(tmp_path), "-t", "prod", "--accept-drift"],
        ME, FakeResponse(payload=UPDATE), FakeResponse(payload=BY_HAND),
        FakeResponse(payload=EXISTING), FakeResponse(payload={"name": "load"}),
        FakeResponse(payload=EXISTING), FakeResponse(payload={"name": "nightly"}),
    )
    assert code == 0
    assert [json.loads(line) for line in out.splitlines()] == [
        {"action": "updated", "name": "load"},
        {"action": "updated", "name": "nightly", "modified": True},
    ]
    assert methods(session).count("PUT") == 2


def test_without_the_setting_a_changed_pipeline_is_only_a_warning(tmp_path):
    make(tmp_path)
    code, _, err, _ = run(
        ["bundle", "plan", str(tmp_path), "-t", "prod"], ME, FakeResponse(payload=UPDATE), FakeResponse(payload=BY_HAND)
    )
    assert code == 0 and "drift: block" not in err and "warning: nightly" in err
