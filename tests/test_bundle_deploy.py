"""``stardelt bundle plan`` and ``deploy`` (stardelt.bundle.render and deploy). No network: a fake session stands in."""

import io
import json
import shutil
import subprocess

import pytest

from stardelt import bundle, cli
from tests.test_api import CANARY, FakeResponse, FakeSession
from tests.test_bundle import make

ENV = {"STARDELT_NOVA_URL": "https://nova.example", "STARDELT_TOKEN": CANARY}
ME = FakeResponse(payload={"user": "alice"})
CREATE = {"ok": True, "action": "create", "errors": [], "changes": [], "nextRuns": ["2026-11-02T03:00:00+01:00"]}
UPDATE = {"ok": True, "action": "update", "errors": [], "changes": ["statement"], "nextRuns": []}
LOAD = {
    "name": "load",
    "ownerGroup": "sales",
    "sql": "select 1",
    "schedule": {"cron": "0 3 * * *", "timezone": "Europe/Berlin"},
}


def run(argv, *responses):
    out, err = io.StringIO(), io.StringIO()
    session = FakeSession(*responses)
    code = cli.main(argv, environ=ENV, stdout=out, stderr=err, session=session, stdin=io.StringIO(""))
    return code, out.getvalue(), err.getvalue(), session


def calls(session):
    return [(method, url.removeprefix("https://nova.example")) for method, url, _ in session.calls]


def test_a_production_target_is_rendered_as_written_with_the_sql_file_read_in(tmp_path):
    problems, mode, specs = bundle.render(make(tmp_path), "prod", "alice")
    assert (problems, mode) == ([], "production")
    assert specs == [LOAD, {"name": "nightly", "ownerGroup": "sales", "sql": "select * from sales.demo.trips"}]


def test_a_development_target_deploys_under_the_persons_name_without_a_schedule(tmp_path):
    problems, mode, specs = bundle.render(make(tmp_path), "dev", "Alice O'Neil")
    assert (problems, mode) == ([], "development")
    assert [spec["name"] for spec in specs] == ["dev-alice-o-neil-load", "dev-alice-o-neil-nightly"]
    assert all("schedule" not in spec for spec in specs)
    assert specs[1]["sql"] == "select * from sales_dev.demo.trips"
    problems, _, specs = bundle.render(make(tmp_path), "dev", None)
    assert specs == [] and "sign in first" in problems[0][1]


def test_a_development_name_that_is_too_long_and_a_notebook_file_are_refused(tmp_path):
    long = "name: " + "a" * 33 + "\nownerGroup: sales\nsql: select 1\n"
    problems, _, specs = bundle.render(make(tmp_path, resources={"long.yaml": long}), "dev", "alice")
    assert specs == [] and "longer than 40 characters" in problems[0][1]
    notebook = "name: nb\nownerGroup: sales\nnotebook: {file: notebooks/explore.ipynb}\n"
    problems, _, specs = bundle.render(make(tmp_path, resources={"nb.yaml": notebook}), "prod", "alice")
    assert specs == [] and "cannot be deployed yet" in problems[0][1]


def test_plan_asks_nova_about_every_pipeline_and_writes_nothing(tmp_path):
    make(tmp_path)
    code, out, err, session = run(
        ["bundle", "plan", str(tmp_path), "-t", "prod"], ME, FakeResponse(payload=CREATE), FakeResponse(payload=UPDATE)
    )
    assert (code, err) == (0, "")
    assert [json.loads(line) for line in out.splitlines()] == [
        {"action": "would-create", "name": "load", "nextRuns": ["2026-11-02T03:00:00+01:00"]},
        {"action": "would-update", "name": "nightly", "changes": ["statement"]},
    ]
    assert calls(session) == [("GET", "/api/me"), ("POST", "/api/pipelines/validate"), ("POST", "/api/pipelines/validate")]
    assert session.calls[1][2]["json"] == {**LOAD, "provenance": {"bundle": "sales-etl", "target": "prod"}}, (
        "outside a Git checkout the stamp names the bundle and the target and no commit"
    )


def test_plan_of_a_development_target_sends_the_prefixed_name_and_no_schedule(tmp_path):
    make(tmp_path)
    code, _, _, session = run(
        ["bundle", "plan", str(tmp_path), "-t", "dev"], ME, FakeResponse(payload=CREATE), FakeResponse(payload=CREATE)
    )
    assert code == 0
    assert session.calls[1][2]["json"] == {
        "name": "dev-alice-load",
        "ownerGroup": "sales",
        "sql": "select 1",
        "provenance": {"bundle": "sales-etl", "target": "dev"},
    }


def test_deploy_creates_or_updates_after_every_pipeline_passed(tmp_path):
    make(tmp_path)
    existing = {"pipelines": [{"name": "nightly", "namespace": "stardelt", "ownerGroup": "sales"}]}
    code, out, err, session = run(
        ["bundle", "deploy", str(tmp_path), "-t", "prod"],
        ME, FakeResponse(payload=CREATE), FakeResponse(payload=UPDATE),
        FakeResponse(payload={"pipelines": []}), FakeResponse(201, {"name": "load"}),
        FakeResponse(payload=existing), FakeResponse(payload={"name": "nightly"}),
    )
    assert (code, err) == (0, "")
    assert [json.loads(line) for line in out.splitlines()] == [
        {"action": "created", "name": "load"},
        {"action": "updated", "name": "nightly"},
    ]
    assert calls(session) == [
        ("GET", "/api/me"), ("POST", "/api/pipelines/validate"), ("POST", "/api/pipelines/validate"),
        ("GET", "/api/pipelines"), ("POST", "/api/pipelines"),
        ("GET", "/api/pipelines"), ("PUT", "/api/pipelines/stardelt/nightly"),
    ]


def test_one_refusal_means_nothing_is_written_and_the_sentence_is_shown(tmp_path):
    make(tmp_path)
    refused = {"ok": False, "errors": [{"message": "you are not a member of the group sales"}], "changes": [], "nextRuns": []}
    code, out, err, session = run(
        ["bundle", "deploy", str(tmp_path), "-t", "prod"], ME, FakeResponse(payload=refused), FakeResponse(payload=UPDATE)
    )
    assert code == 2
    assert err == "stardelt: prod: load: you are not a member of the group sales\n"
    assert [json.loads(line)["name"] for line in out.splitlines()] == ["nightly"], "what passed is still reported"
    assert [method for method, _ in calls(session)] == ["GET", "POST", "POST"], "no create, no update"


def test_deploy_dry_run_is_a_plan_and_a_bundle_problem_stops_before_any_request(tmp_path):
    make(tmp_path)
    code, out, _, session = run(
        ["bundle", "deploy", str(tmp_path), "-t", "prod", "--dry-run"], ME, FakeResponse(payload=CREATE), FakeResponse(payload=CREATE)
    )
    assert code == 0 and out.count("would-create") == 2 and len(session.calls) == 3
    code, out, err, session = run(["bundle", "plan", str(tmp_path), "-t", "nope"], ME)
    assert (code, out) == (2, "") and "no target named 'nope'" in err


def git(path, *args):
    done = subprocess.run(
        ["git", "-c", "user.email=a@example.com", "-c", "user.name=t", "-C", str(path), *args],
        capture_output=True, text=True, check=True,
    )
    return done.stdout.strip()


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_a_bundle_in_a_git_checkout_sends_its_commit_and_marks_uncommitted_changes(tmp_path):
    make(tmp_path)
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "bundle")
    commit = git(tmp_path, "rev-parse", "HEAD")
    answers = (ME, FakeResponse(payload=CREATE), FakeResponse(payload=CREATE))

    _, _, _, session = run(["bundle", "plan", str(tmp_path), "-t", "prod"], *answers)
    assert session.calls[1][2]["json"]["provenance"] == {"bundle": "sales-etl", "target": "prod", "revision": commit}

    (tmp_path / "sql" / "load.sql").write_text("select 2")
    _, _, _, session = run(["bundle", "plan", str(tmp_path), "-t", "prod"], *answers)
    assert session.calls[1][2]["json"]["provenance"]["revision"] == commit + "-dirty"
    assert len(commit) == 40 and set(commit) <= set("0123456789abcdef")


def test_the_stamp_names_the_bundle_and_the_target_and_leaves_the_commit_out_without_git(tmp_path):
    make(tmp_path)
    assert bundle.provenance(tmp_path, "dev") == {"bundle": "sales-etl", "target": "dev"}
