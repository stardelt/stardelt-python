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
    "planId": "c" * 64,
    "approvable": True,
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
DEV_MANIFEST = MANIFEST.replace(
    "  prod: {mode: production, runAs: pipeline-sales}",
    "  dev: {mode: development, runAs: pipeline-sales}",
)
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


def test_a_production_deploy_sends_the_mode_and_the_plan_id(tmp_path):
    build(tmp_path)
    code, out, err, session = run(
        ["bundle", "deploy", str(tmp_path), "-t", "prod"],
        ME, FakeResponse(payload=PLAN), FakeResponse(payload=CREATE), FakeResponse(payload=APPLIED),
        FakeResponse(payload={"pipelines": []}), FakeResponse(status=201, payload={"name": "load"}),
    )
    assert (code, err) == (0, "")
    apply_call = [call for call in session.calls if call[1].endswith("/api/migrations/apply")][0]
    assert apply_call[2]["json"]["mode"] == "production"
    assert apply_call[2]["json"]["approvalId"] == "c" * 64


def test_a_development_deploy_sends_development_and_no_approval(tmp_path):
    build(tmp_path, manifest=DEV_MANIFEST)
    code, out, err, session = run(
        ["bundle", "deploy", str(tmp_path), "-t", "dev"],
        ME, FakeResponse(payload=PLAN), FakeResponse(payload=CREATE), FakeResponse(payload=APPLIED),
        FakeResponse(payload={"pipelines": []}), FakeResponse(status=201, payload={"name": "dev-alice-load"}),
    )
    assert (code, err) == (0, "")
    apply_call = [call for call in session.calls if call[1].endswith("/api/migrations/apply")][0]
    assert apply_call[2]["json"]["mode"] == "development"
    assert apply_call[2]["json"]["approvalId"] == ""


def test_bundle_approve_calls_the_new_route():
    code, out, err, session = run(
        ["bundle", "approve", "c" * 64],
        FakeResponse(payload={"planId": "c" * 64, "approver": "Bob", "approvedAt": 1}),
    )
    assert (code, err) == (0, "")
    assert json.loads(out) == {"planId": "c" * 64, "approver": "Bob", "approvedAt": 1}
    assert calls(session) == [("POST", "/api/migrations/approve")]
    assert session.calls[0][2]["json"] == {"planId": "c" * 64}


def test_a_refused_production_deploy_prints_nova_s_sentence(tmp_path):
    build(tmp_path)
    sentence = "a breaking change in a production target needs a second person: stardelt bundle approve abc"
    refused = FakeResponse(status=409, payload={"ok": False, "applied": [], "skipped": [], "errors": [sentence]})
    code, out, err, session = run(
        ["bundle", "deploy", str(tmp_path), "-t", "prod"],
        ME, FakeResponse(payload=PLAN), FakeResponse(payload=CREATE), refused,
    )
    assert code == 2
    assert err.startswith("stardelt: prod: migrations: ")
    assert sentence in err


BREAKING = {
    "sql/migrations/001_init.sql": "create table trips (id int)",
    "sql/migrations/002_drop.sql": "drop table warehouse.sales.trips",
}
BREAKING_PLAN = {
    "ok": True,
    "ledger": True,
    "planId": "",
    "approvable": False,
    "files": [
        {"name": "001_init.sql", "sha256": "a" * 64, "class": "additive", "status": "applied", "statements": 1},
        {"name": "002_drop.sql", "sha256": "b" * 64, "class": "breaking", "status": "new", "statements": 1,
         "reason": "drop table warehouse.sales.trips"},
    ],
    "errors": [],
}
IMPACT = {"nodes": [], "edges": [], "jobs": [], "impact": {"downstream": 3, "upstream": 0, "downstreamJobs": 2}}


def test_a_breaking_migration_names_the_table_and_its_downstream_count(tmp_path):
    build(tmp_path, migrations=BREAKING)
    code, out, err, session = run(
        ["bundle", "plan", str(tmp_path), "-t", "prod"],
        ME, FakeResponse(payload=BREAKING_PLAN), FakeResponse(payload=IMPACT), FakeResponse(payload=CREATE),
    )
    assert (code, err) == (0, "")
    assert [json.loads(line) for line in out.splitlines()] == [
        {"action": "migration-in-ledger", "name": "001_init.sql"},
        {"action": "would-apply-migration", "name": "002_drop.sql", "class": "breaking",
         "impact": {"table": "warehouse.sales.trips", "downstream": 3, "downstreamJobs": 2}},
        {"action": "would-create", "name": "load", "nextRuns": ["2026-11-02T03:00:00+01:00"]},
    ]
    assert calls(session) == [
        ("GET", "/api/me"),
        ("POST", "/api/migrations/plan"),
        ("GET", "/api/lineage"),
        ("POST", "/api/pipelines/validate"),
    ]
    assert session.calls[2][2]["params"] == {
        "catalog": "warehouse", "schema": "sales", "table": "trips", "depth": 1, "days": 30,
    }


def test_a_breaking_file_without_a_table_draws_no_impact_line_and_makes_no_extra_call(tmp_path):
    build(tmp_path, migrations={**BREAKING, "sql/migrations/002_drop.sql": "insert into warehouse.sales.trips values (1)"})
    plan = {**BREAKING_PLAN, "files": [BREAKING_PLAN["files"][0], {**BREAKING_PLAN["files"][1], "reason": "insert into warehouse.sales.trips values (1)"}]}
    code, out, err, session = run(
        ["bundle", "plan", str(tmp_path), "-t", "prod"],
        ME, FakeResponse(payload=plan), FakeResponse(payload=CREATE),
    )
    assert (code, err) == (0, "")
    rows = [json.loads(line) for line in out.splitlines()]
    assert rows[1] == {"action": "would-apply-migration", "name": "002_drop.sql", "class": "breaking"}
    assert calls(session) == [("GET", "/api/me"), ("POST", "/api/migrations/plan"), ("POST", "/api/pipelines/validate")]


def test_an_unavailable_lineage_answer_leaves_the_plan_intact(tmp_path):
    build(tmp_path, migrations=BREAKING)
    code, out, err, session = run(
        ["bundle", "plan", str(tmp_path), "-t", "prod"],
        ME, FakeResponse(payload=BREAKING_PLAN), FakeResponse(status=503, payload={"error": "the state store is not configured"}),
        FakeResponse(payload=CREATE),
    )
    assert (code, err) == (0, "")
    rows = [json.loads(line) for line in out.splitlines()]
    assert rows[1] == {"action": "would-apply-migration", "name": "002_drop.sql", "class": "breaking"}
    assert calls(session)[2] == ("GET", "/api/lineage")
