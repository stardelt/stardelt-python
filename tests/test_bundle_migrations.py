"""A bundle's ``migrations:`` block: offline validate, plan and deploy (stardelt.bundle). No network: a fake session stands in."""

import io
import json

from stardelt import bundle, cli

from tests.test_api import CANARY, FakeResponse, FakeSession

ENV = {"STARDELT_NOVA_URL": "https://nova.example", "STARDELT_TOKEN": CANARY}
ME = FakeResponse(payload={"user": "alice"})
CREATE = {"ok": True, "action": "create", "errors": [], "changes": [], "nextRuns": ["2026-11-02T03:00:00+01:00"]}
PLAN = {
    "ok": True,
    "ledger": True,
    "files": [
        {"name": "001_init.sql", "sha256": "a" * 64, "class": "additive", "status": "applied", "statements": 1},
        {"name": "002_add.sql", "sha256": "b" * 64, "class": "additive", "status": "new", "statements": 1},
    ],
    "errors": [],
}
APPLIED = {"ok": True, "applied": ["002_add.sql"], "skipped": ["001_init.sql"], "errors": []}
MANIFEST = """
bundle: {name: sales-etl, ownerGroup: sales}
include: [resources/*.yaml]
files: [sql/migrations/*.sql]
migrations:
  catalog: warehouse
  files: [sql/migrations/*.sql]
targets:
  prod: {mode: production, runAs: pipeline-sales}
"""
RESOURCE = "name: load\nownerGroup: sales\nsql: select 1\n"
MIGRATIONS = {
    "sql/migrations/001_init.sql": "create table trips (id int)",
    "sql/migrations/002_add.sql": "alter table trips add column day int",
}


def build(tmp_path, manifest=MANIFEST, migrations=MIGRATIONS):
    (tmp_path / "bundle.yaml").write_text(manifest)
    (tmp_path / "resources").mkdir(exist_ok=True)
    (tmp_path / "resources" / "load.yaml").write_text(RESOURCE)
    for name, text in migrations.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return tmp_path


def run(argv, *responses):
    out, err = io.StringIO(), io.StringIO()
    session = FakeSession(*responses)
    code = cli.main(argv, environ=ENV, stdout=out, stderr=err, session=session, stdin=io.StringIO(""))
    return code, out.getvalue(), err.getvalue(), session


def calls(session):
    return [(method, url.removeprefix("https://nova.example")) for method, url, _ in session.calls]


def test_offline_validate_reports_a_bad_catalog_and_a_bad_file_name_without_a_request(tmp_path):
    manifest = MANIFEST.replace("catalog: warehouse", "catalog: Bad_Catalog")
    build(tmp_path, manifest=manifest, migrations={"sql/migrations/1_x.sql": "select 1"})
    code, out, err, session = run(["bundle", "validate", str(tmp_path)])
    assert (code, out) == (2, "")
    assert "stardelt: migrations.catalog: must be a catalog name" in err
    assert "stardelt: sql/migrations/1_x.sql: a migration is named NNN_<words>.sql" in err
    assert session.calls == [], "a bundle is checked offline"


def test_two_migration_files_with_one_basename_are_refused(tmp_path):
    manifest = MANIFEST.replace("files: [sql/migrations/*.sql]", "files: [sql/migrations/*.sql, sql/other/*.sql]")
    build(
        tmp_path,
        manifest=manifest,
        migrations={"sql/migrations/001_a.sql": "select 1", "sql/other/001_a.sql": "select 2"},
    )
    problems = bundle.validate(tmp_path)[0]
    assert [(where, message) for where, message in problems if "001_a.sql" in message] == [
        ("sql/other/001_a.sql", "two migration files are named 001_a.sql"),
    ]


def test_plan_lists_migrations_before_pipelines_and_writes_nothing(tmp_path):
    build(tmp_path)
    code, out, err, session = run(
        ["bundle", "plan", str(tmp_path), "-t", "prod"], ME, FakeResponse(payload=PLAN), FakeResponse(payload=CREATE)
    )
    assert (code, err) == (0, "")
    assert [json.loads(line) for line in out.splitlines()] == [
        {"action": "migration-in-ledger", "name": "001_init.sql"},
        {"action": "would-apply-migration", "name": "002_add.sql", "class": "additive"},
        {"action": "would-create", "name": "load", "nextRuns": ["2026-11-02T03:00:00+01:00"]},
    ]
    assert calls(session) == [("GET", "/api/me"), ("POST", "/api/migrations/plan"), ("POST", "/api/pipelines/validate")]
    assert session.calls[1][2]["json"]["files"] == [
        {"name": "001_init.sql", "sha256": bundle.hashlib.sha256(b"create table trips (id int)").hexdigest(), "sql": "create table trips (id int)"},
        {"name": "002_add.sql", "sha256": bundle.hashlib.sha256(b"alter table trips add column day int").hexdigest(), "sql": "alter table trips add column day int"},
    ]


def test_deploy_applies_migrations_before_the_pipelines(tmp_path):
    build(tmp_path)
    code, out, err, session = run(
        ["bundle", "deploy", str(tmp_path), "-t", "prod"],
        ME, FakeResponse(payload=PLAN), FakeResponse(payload=CREATE), FakeResponse(payload=APPLIED),
        FakeResponse(payload={"pipelines": []}), FakeResponse(201, {"name": "load"}),
    )
    assert (code, err) == (0, "")
    assert [json.loads(line) for line in out.splitlines()] == [
        {"action": "migration-in-ledger", "name": "001_init.sql"},
        {"action": "migration-applied", "name": "002_add.sql"},
        {"action": "created", "name": "load"},
    ]
    assert [method for method, _ in calls(session)] == ["GET", "POST", "POST", "POST", "GET", "POST"]
    apply_call = [call for call in session.calls if call[1].endswith("/api/migrations/apply")][0]
    assert apply_call[2]["json"]["allowBreaking"] is False


def test_a_refused_breaking_migration_stops_the_deploy_and_allow_breaking_passes_the_flag(tmp_path):
    build(tmp_path)
    refused = {
        "ok": False,
        "applied": [],
        "skipped": [],
        "errors": ["002_add.sql: drop column is breaking; pass allowBreaking"],
    }
    answers = (ME, FakeResponse(payload=PLAN), FakeResponse(payload=CREATE), FakeResponse(payload=refused))
    code, out, err, session = run(["bundle", "deploy", str(tmp_path), "-t", "prod"], *answers)
    assert code == 2
    assert err == "stardelt: prod: migrations: 002_add.sql: drop column is breaking; pass allowBreaking\n"
    assert calls(session) == [
        ("GET", "/api/me"), ("POST", "/api/migrations/plan"), ("POST", "/api/pipelines/validate"),
        ("POST", "/api/migrations/apply"),
    ], "no pipeline is written"

    code, _, _, session = run(["bundle", "deploy", str(tmp_path), "-t", "prod", "--allow-breaking"], *answers)
    assert code == 2
    assert session.calls[-1][2]["json"]["allowBreaking"] is True
