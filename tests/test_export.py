"""Tests for ``stardelt export`` (stardelt.apply.export). No network: a fake session stands in."""

import io
import json

from stardelt import apply, cli
from tests.test_api import CANARY, FakeResponse, FakeSession

ENV = {"STARDELT_NOVA_URL": "https://nova.example", "STARDELT_TOKEN": CANARY}
OPTIONS = {"retries": 1, "backoffMinutes": 5, "timeoutMinutes": 120, "concurrency": "Forbid"}
DOCUMENT = {
    "name": "daily",
    "ownerGroup": "etl",
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


def test_a_pipeline_is_written_as_yaml_with_the_statement_as_a_block():
    code, out, err, session = run(["export", "stardelt", "daily"], FakeResponse(payload=ANSWER))
    assert (code, err) == (0, "")
    assert [(m, u) for m, u, _ in session.calls] == [("GET", "https://nova.example/api/pipelines/stardelt/daily/spec")]
    assert out == (
        "name: daily\n"
        "ownerGroup: etl\n"
        "sql: |-\n"
        "  select a,\n"
        "         b\n"
        "  from t\n"
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


def test_what_export_writes_is_what_apply_reads_back():
    _, out, _, _ = run(["export", "stardelt", "daily"], FakeResponse(payload=ANSWER))
    assert apply.load(out) == DOCUMENT
    _, as_json, _, _ = run(["export", "stardelt", "daily", "--format", "json"], FakeResponse(payload=ANSWER))
    assert apply.load(as_json) == DOCUMENT
    assert json.loads(as_json) == DOCUMENT


def test_a_pipeline_nova_cannot_change_is_exported_with_a_note():
    outside = dict(ANSWER, managedBy="other", editable=False)
    code, out, err, _ = run(["export", "stardelt", "daily"], FakeResponse(payload=outside))
    assert code == 0 and out.startswith("name: daily\n")
    assert err == (
        "stardelt: note: daily is managed outside Nova (kubectl or GitOps): this file shows what Nova can read of it, "
        "and `stardelt apply` cannot change it\n"
    )
    odd = dict(ANSWER, editable=False)
    _, _, err, _ = run(["export", "stardelt", "daily"], FakeResponse(payload=odd))
    assert err == "stardelt: note: daily has a shape the form cannot edit: this file shows only what Nova can read of it\n"


def test_a_refusal_ends_the_command_with_nova_s_sentence():
    code, out, err, _ = run(["export", "stardelt", "gone"], FakeResponse(404, {"error": "no such pipeline"}))
    assert (code, out) == (1, "")
    assert err == "stardelt: no such pipeline (HTTP 404)\n"
