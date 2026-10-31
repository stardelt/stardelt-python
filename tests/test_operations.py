"""The contract-driven part of the SDK and the CLI: operations.json, Client.call and `stardelt api`."""

import importlib.util
import io
import json
import re
from pathlib import Path

import pytest

import stardelt.api as api
from stardelt import cli
from tests.test_api import FakeResponse, FakeSession

ROOT = Path(__file__).parent.parent
ENV = {"STARDELT_NOVA_URL": "https://nova.example", "STARDELT_TOKEN": "t"}


def sync_module():
    spec = importlib.util.spec_from_file_location("sync_contract", ROOT / "scripts" / "sync_contract.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(argv, *responses, environ=ENV):
    out, err = io.StringIO(), io.StringIO()
    session = FakeSession(*responses)
    code = cli.main(argv, environ=environ, stdout=out, stderr=err, session=session)
    return code, out.getvalue(), err.getvalue(), session


def test_the_shipped_operations_are_well_formed():
    ops = api.operations()
    assert len(ops) >= 40
    for name in ("me", "query_run", "pipelines_get", "table_history", "query_log_list"):
        assert name in ops, name
    for op_id, op in ops.items():
        assert op["method"] in {"GET", "POST", "PUT", "DELETE", "PATCH"}, op_id
        assert op["path"].startswith("/api/"), op_id
        assert op["path_params"] == re.findall(r"\{(\w+)\}", op["path"]), op_id
        assert re.fullmatch(r"[a-z0-9]+(_[a-z0-9]+)*", op_id), op_id


def test_the_sync_script_keeps_public_operations_only_and_sorts_them(tmp_path):
    out = tmp_path / "operations.json"
    code = sync_module().main(["--contract", str(ROOT / "tests" / "data" / "contract-mini.json"), "--out", str(out)])
    assert code == 0
    assert json.loads(out.read_text()) == {
        "contract_version": "9.9.9",
        "operations": {
            "things_get": {"method": "GET", "path": "/api/things/{id}", "path_params": ["id"],
                           "query_params": ["verbose"], "body": False},
            "things_update": {"method": "PUT", "path": "/api/things/{id}", "path_params": ["id"],
                              "query_params": [], "body": True},
        },
    }


def client(*responses):
    session = FakeSession(*responses)
    return api.Client(base_url="https://nova.example", token="t", session=session), session


def test_call_fills_and_quotes_path_parameters_and_sends_query_and_body():
    c, session = client(FakeResponse(payload={"ok": True}))
    answer = c.call("table_history", path={"ns": "a b", "tbl": "t/x"}, query={"catalog": "main"})
    assert answer == {"ok": True}
    method, url, kwargs = session.calls[0]
    assert (method, url) == ("GET", "https://nova.example/api/catalog/tables/a%20b/t%2Fx/history")
    assert kwargs["params"] == {"catalog": "main"} and "json" not in kwargs
    c, session = client(FakeResponse(payload={}))
    c.call("query_run", body={"sql": "select 1"})
    assert session.calls[0][0:2] == ("POST", "https://nova.example/api/query")
    assert session.calls[0][2]["json"] == {"sql": "select 1"}


def test_call_returns_none_for_an_empty_answer():
    c, _ = client(FakeResponse(status=204))
    assert c.call("saved_queries_remove", path={"id": "7"}) is None


def test_call_refuses_an_unknown_operation_and_wrong_path_parameters():
    c, session = client()
    with pytest.raises(ValueError, match="unknown operation"):
        c.call("no_such_operation")
    with pytest.raises(ValueError, match="missing .*name"):
        c.call("pipelines_get", path={"namespace": "a"})
    with pytest.raises(ValueError, match="unexpected .*extra"):
        c.call("me", path={"extra": "1"})
    assert session.calls == []


def test_stardelt_api_list_needs_no_host_and_no_token():
    code, out, err, session = run(["api", "--list"], environ={})
    assert (code, err, session.calls) == (0, "", [])
    lines = out.splitlines()
    assert lines == sorted(lines) and any(re.fullmatch(r"me\s+GET\s+/api/me", line) for line in lines)


def test_stardelt_api_calls_an_operation_and_prints_the_json():
    code, out, _, session = run(
        ["api", "pipelines_get", "-p", "namespace=a", "-p", "name=b", "-q", "x=1"],
        FakeResponse(payload={"name": "b"}),
    )
    assert code == 0 and json.loads(out) == {"name": "b"}
    method, url, kwargs = session.calls[0]
    assert (method, url, kwargs["params"]) == ("GET", "https://nova.example/api/pipelines/a/b", {"x": "1"})


def test_stardelt_api_sends_a_json_body_and_is_silent_on_204():
    code, out, _, session = run(
        ["api", "saved_queries_create", "--body", '{"title": "t", "sql": "select 1"}'],
        FakeResponse(status=204),
    )
    assert (code, out) == (0, "")
    assert session.calls[0][2]["json"] == {"title": "t", "sql": "select 1"}


def test_stardelt_api_usage_mistakes_exit_2_before_any_request():
    for argv in (
        ["api"],
        ["api", "no_such_operation"],
        ["api", "pipelines_get", "-p", "namespace=a"],
        ["api", "me", "-p", "broken"],
        ["api", "query_run", "--body", "{not json"],
    ):
        code, out, err, session = run(argv)
        assert (code, out, session.calls) == (2, "", []) and err.startswith("stardelt: "), argv


ITEMS = {"items": [{"id": "q1", "state": "FINISHED", "sql": "select 1"}, {"id": "q2", "state": "FAILED"}], "nextCursor": None}


def test_the_table_format_prints_the_one_list_of_objects_in_an_answer():
    code, out, _, _ = run(["api", "query_log_list", "--format", "table"], FakeResponse(payload=ITEMS))
    assert code == 0
    assert out == "id  state     sql\nq1  FINISHED  select 1\nq2  FAILED\n"


def test_the_table_format_takes_a_top_level_list_and_cuts_long_and_nested_cells():
    payload = [{"name": "a", "tags": ["x", "y"], "text": "w" * 80}]
    code, out, _, _ = run(["api", "warehouses_list", "--format", "table"], FakeResponse(payload=payload))
    assert code == 0
    header, row = out.splitlines()
    assert header.split() == ["name", "tags", "text"]
    assert '["x", "y"]' in row and row.endswith("w" * 39 + "…")


def test_the_table_format_falls_back_to_json_when_the_answer_is_not_a_list_of_objects():
    code, out, _, _ = run(["api", "me", "--format", "table"], FakeResponse(payload={"user": "analyst"}))
    assert (code, json.loads(out)) == (0, {"user": "analyst"})
    code, out, _, _ = run(["api", "query_log_list", "--format", "table"], FakeResponse(payload={"items": [], "nextCursor": None}))
    assert (code, json.loads(out)) == (0, {"items": [], "nextCursor": None})
