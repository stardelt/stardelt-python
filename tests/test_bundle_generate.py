"""``stardelt bundle generate`` (stardelt.bundle.generate): a pipeline made in the interface becomes files of a bundle."""

import io

from stardelt import bundle, cli
from tests.test_api import CANARY, FakeResponse, FakeSession
from tests.test_bundle import make

ENV = {"STARDELT_NOVA_URL": "https://nova.example", "STARDELT_TOKEN": CANARY}
OPTIONS = {"retries": 1, "backoffMinutes": 5, "timeoutMinutes": 120, "concurrency": "Forbid"}
DOCUMENT = {
    "name": "daily",
    "ownerGroup": "sales",
    "sql": "select a,\n       b\nfrom t",
    "schedule": {"cron": "0 3 * * *", "timezone": "Europe/Berlin"},
    "params": {"day": "2026-11-01"},
    "options": OPTIONS,
}
ANSWER = {"document": DOCUMENT, "resourceVersion": "7", "managedBy": "nova", "editable": True}


def run(argv, *responses):
    out, err = io.StringIO(), io.StringIO()
    session = FakeSession(*responses)
    code = cli.main(argv, environ=ENV, stdout=out, stderr=err, session=session, stdin=io.StringIO(""))
    return code, out.getvalue(), err.getvalue(), session


def generate(directory, answer=ANSWER, name="daily"):
    return run(["bundle", "generate", "stardelt", name, str(directory)], FakeResponse(payload=answer))


def test_a_statement_pipeline_becomes_a_pipeline_file_and_a_sql_file(tmp_path):
    make(tmp_path)
    code, out, err, session = generate(tmp_path)
    assert (code, out) == (0, "")
    assert err == (
        f"stardelt: wrote resources/daily.yaml, sql/daily.sql in {tmp_path}; now: stardelt bundle validate {tmp_path}\n"
    )
    assert [(m, u) for m, u, _ in session.calls] == [("GET", "https://nova.example/api/pipelines/stardelt/daily/spec")]
    assert (tmp_path / "resources" / "daily.yaml").read_text() == (
        "name: daily\n"
        "sqlFile: sql/daily.sql\n"
        "schedule:\n"
        "  cron: 0 3 * * *\n"
        "  timezone: Europe/Berlin\n"
        "params:\n"
        "  day: '2026-11-01'\n"
        "options:\n"
        "  retries: 1\n"
        "  backoffMinutes: 5\n"
        "  timeoutMinutes: 120\n"
        "  concurrency: Forbid\n"
    )
    assert (tmp_path / "sql" / "daily.sql").read_text() == "select a,\n       b\nfrom t\n"


def test_what_generate_writes_validates_and_deploys_as_the_pipeline_it_came_from(tmp_path):
    make(tmp_path)
    generate(tmp_path)
    problems, _, _ = bundle.validate(tmp_path)
    assert problems == []
    problems, _, specs = bundle.render(tmp_path, "prod", "alice")
    assert problems == []
    daily = next(spec for spec in specs if spec["name"] == "daily")
    # Nova trims a statement, so the line break the file ends with is no change.
    assert daily == {**DOCUMENT, "sql": DOCUMENT["sql"] + "\n"}


def test_a_notebook_pipeline_needs_no_sql_file(tmp_path):
    make(tmp_path)
    notebook = {"name": "nb", "ownerGroup": "sales", "notebook": {"name": "explore", "version": 3}}
    code, _, err, _ = generate(tmp_path, dict(ANSWER, document=notebook), "nb")
    assert code == 0 and "wrote resources/nb.yaml in" in err
    assert (tmp_path / "resources" / "nb.yaml").read_text() == "name: nb\nnotebook:\n  name: explore\n  version: 3\n"
    assert not (tmp_path / "sql" / "nb.sql").exists()


def test_nothing_is_overwritten(tmp_path):
    make(tmp_path)
    code, _, err, _ = generate(tmp_path, name="load")
    assert code == 2 and "resources/load.yaml already exists: generate does not overwrite it" in err
    assert (tmp_path / "sql" / "load.sql").read_text() == "select 1"

    (tmp_path / "other").mkdir()
    only_sql = make(tmp_path / "other", resources={}, files={"sql/daily.sql": "keep"})
    code, _, err, _ = generate(only_sql)
    assert code == 2 and "sql/daily.sql already exists" in err
    assert not (only_sql / "resources" / "daily.yaml").exists(), "a refusal writes nothing"


def test_a_pipeline_a_file_cannot_change_or_of_another_group_is_refused_and_nothing_is_written(tmp_path):
    make(tmp_path)
    other = dict(ANSWER, document=dict(DOCUMENT, ownerGroup="etl"))
    code, _, err, _ = generate(tmp_path, other)
    assert code == 2 and "daily belongs to the group etl, the bundle to sales" in err
    for answer in (dict(ANSWER, managedBy="kubectl"), dict(ANSWER, editable=False)):
        code, _, err, _ = generate(tmp_path, answer)
        assert code == 2 and "daily cannot be changed from a file" in err
    assert not (tmp_path / "resources" / "daily.yaml").exists()
    assert not (tmp_path / "sql" / "daily.sql").exists()


def test_a_bundle_that_would_not_pick_the_files_up_and_a_missing_manifest_are_refused(tmp_path):
    manifest = "bundle: {name: sales-etl, ownerGroup: sales}\ninclude: [pipelines/*.yaml]\nfiles: [queries/*.sql]\ntargets: {}\n"
    make(tmp_path, manifest=manifest, resources={}, files={})
    code, _, err, _ = generate(tmp_path)
    assert code == 2 and "resources/daily.yaml would not be picked up: add a pattern for it under include in bundle.yaml" in err
    assert not (tmp_path / "resources" / "daily.yaml").exists()

    code, _, err, session = run(["bundle", "generate", "stardelt", "daily", str(tmp_path / "nowhere")])
    assert code == 2 and "bundle.yaml is missing or cannot be read: write one with `stardelt bundle init`" in err
    assert session.calls == [], "nothing is asked of Nova when there is no bundle"
