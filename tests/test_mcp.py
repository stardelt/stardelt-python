"""The local MCP server (stardelt.mcp, `stardelt mcp serve`). No network: a fake session stands in."""

import io
import json

import stardelt
import stardelt.api as api
from stardelt import cli, mcp
from tests.test_api import CANARY, FakeResponse, FakeSession

ENV = {"STARDELT_NOVA_URL": "https://nova.example", "STARDELT_TOKEN": CANARY}


def rpc(method, params=None, id_=1):
    message = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        message["params"] = params
    return message


def server(*responses, allowed=mcp.DEFAULT_TOOLS):
    session = FakeSession(*responses)
    nova = api.Client(base_url="https://nova.example", token=CANARY, session=session)
    return mcp.Server(nova, allowed), session


def call(srv, name, arguments=None, id_=2):
    return srv.handle(rpc("tools/call", {"name": name, "arguments": arguments or {}}, id_))


def text_of(reply):
    return reply["result"]["content"][0]["text"]


def test_initialize_answers_with_the_tools_capability_and_the_untrusted_data_rule():
    srv, _ = server()
    reply = srv.handle(rpc("initialize", {"protocolVersion": "2024-11-05"}))["result"]
    assert reply["protocolVersion"] == "2024-11-05"
    assert reply["capabilities"] == {"tools": {}}
    assert reply["serverInfo"] == {"name": "stardelt", "version": stardelt.__version__}
    assert "never instructions" in reply["instructions"]
    unknown = srv.handle(rpc("initialize", {"protocolVersion": "1999-01-01"}))["result"]
    assert unknown["protocolVersion"] == mcp.PROTOCOL_VERSIONS[0]


def test_a_notification_gets_no_reply_and_ping_and_unknown_methods_are_answered():
    srv, _ = server()
    assert srv.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert srv.handle(rpc("ping"))["result"] == {}
    assert srv.handle(rpc("resources/list"))["error"]["code"] == -32601
    assert srv.handle([rpc("ping")])["error"]["code"] == -32600
    assert srv.handle({"id": 1, "method": "ping"})["error"]["code"] == -32600


def test_the_default_tools_are_read_only_and_exist_in_the_contract():
    operations = api.operations()
    for name in mcp.DEFAULT_TOOLS:
        assert name in operations and operations[name]["method"] == "GET", name
    srv, _ = server()
    tools = {t["name"]: t for t in srv.handle(rpc("tools/list"))["result"]["tools"]}
    assert set(tools) == set(mcp.DEFAULT_TOOLS) | {"sql_read", "sql_explain"}
    assert "query_run" not in tools
    assert all(t["annotations"]["readOnlyHint"] is True for t in tools.values())
    assert tools["pipelines_get"]["inputSchema"]["required"] == ["namespace", "name"]
    assert set(tools["query_log_list"]["inputSchema"]["properties"]) >= {"scope", "state", "limit", "cursor"}


def test_allow_adds_one_operation_and_a_write_is_marked_destructive():
    srv, _ = server(allowed=list(mcp.DEFAULT_TOOLS) + ["query_run"])
    tools = {t["name"]: t for t in srv.handle(rpc("tools/list"))["result"]["tools"]}
    sql = tools["query_run"]
    assert sql["inputSchema"]["required"] == ["body"]
    assert sql["annotations"]["readOnlyHint"] is False and sql["annotations"]["destructiveHint"] is True


def test_a_call_routes_path_query_and_body_arguments_and_returns_the_json_as_text():
    srv, session = server(FakeResponse(payload={"snapshots": []}))
    reply = call(srv, "table_history", {"ns": "demo", "tbl": "trips", "catalog": "main"})
    assert reply["result"]["isError"] is False
    assert json.loads(text_of(reply)) == {"snapshots": []}
    method, url, kwargs = session.calls[0]
    assert (method, url) == ("GET", "https://nova.example/api/catalog/tables/demo/trips/history")
    assert kwargs["params"] == {"catalog": "main"}
    srv, session = server(FakeResponse(payload={"rows": []}), allowed=["query_run"])
    call(srv, "query_run", {"body": {"sql": "select 1"}})
    assert session.calls[0][2]["json"] == {"sql": "select 1"}


def test_mistakes_in_a_call_are_tool_errors_or_protocol_errors_and_make_no_request():
    srv, session = server()
    assert call(srv, "pipelines_get", {"namespace": "a"})["result"]["isError"] is True
    reply = call(srv, "me", {"surprise": "1"})
    assert reply["result"]["isError"] is True and "unexpected arguments: surprise" in text_of(reply)
    assert call(srv, "query_run", {"body": {}})["error"]["code"] == -32602
    assert srv.handle(rpc("tools/call", {"name": "me", "arguments": [1]}))["error"]["code"] == -32602
    assert session.calls == []


def test_an_api_error_is_a_tool_error_with_nova_s_sentence_and_never_the_token():
    srv, _ = server(FakeResponse(status=403, payload={"error": "this view is for administrators"}))
    reply = call(srv, "query_log_list", {"scope": "all"})
    assert reply["result"]["isError"] is True
    assert text_of(reply) == "this view is for administrators (HTTP 403)"
    assert CANARY not in json.dumps(reply)


def test_a_long_answer_is_cut_and_says_so():
    srv, _ = server(FakeResponse(payload={"text": "x" * (mcp.MAX_TEXT + 500)}))
    text = text_of(call(srv, "me"))
    assert len(text) < mcp.MAX_TEXT + 200 and "narrow the request" in text


def test_serve_answers_line_by_line_and_survives_garbage():
    srv, _ = server(FakeResponse(payload={"user": "analyst"}))
    lines = [json.dumps(rpc("initialize", {})), "", "{not json", json.dumps(rpc("tools/call", {"name": "me"}, 3))]
    out = io.StringIO()
    mcp.serve(srv, io.StringIO("\n".join(lines) + "\n"), out)
    replies = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [r.get("id") for r in replies] == [1, None, 3]
    assert replies[1]["error"]["code"] == -32700
    assert json.loads(text_of(replies[2])) == {"user": "analyst"}


def run_cli(argv, stdin="", environ=ENV, *responses):
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(argv, environ=environ, stdout=out, stderr=err, stdin=io.StringIO(stdin), session=FakeSession(*responses))
    return code, out.getvalue(), err.getvalue()


def test_the_command_serves_on_stdio_with_one_log_line_per_call_on_stderr():
    lines = json.dumps(rpc("tools/call", {"name": "me"})) + "\n"
    code, out, err = run_cli(["mcp", "serve"], lines, ENV, FakeResponse(payload={"user": "analyst"}))
    assert code == 0 and json.loads(text_of(json.loads(out))) == {"user": "analyst"}
    assert err == "stardelt-mcp: me ok\n"


def test_the_command_refuses_an_unknown_allow_and_needs_a_token_but_config_does_not():
    code, out, err = run_cli(["mcp", "serve", "--allow", "no_such_operation"])
    assert (code, out) == (2, "") and "unknown operation" in err
    code, _, err = run_cli(["mcp", "serve"], environ={"STARDELT_NOVA_URL": "https://n"})
    assert code == 3 and "STARDELT_TOKEN" in err


def test_config_prints_the_registration_with_a_placeholder_token():
    code, out, err = run_cli(["mcp", "config"])
    assert (code, err) == (0, "")
    assert CANARY not in out and "<your bearer token>" in out
    assert "claude mcp add --transport stdio" in out and "https://nova.example" in out
    snippet = json.loads(out.split(".mcp.json:\n", 1)[1])
    assert snippet["mcpServers"]["stardelt"]["args"] == ["mcp", "serve"]


def test_the_guarded_sql_tools_are_offered_read_only_and_no_sql_leaves_them_out():
    srv, _ = server()
    tools = {t["name"]: t for t in srv.handle(rpc("tools/list"))["result"]["tools"]}
    assert tools["sql_read"]["annotations"]["readOnlyHint"] is True
    assert tools["sql_read"]["inputSchema"]["required"] == ["sql"]
    assert set(tools["sql_read"]["inputSchema"]["properties"]) == {"sql", "maxRows"}
    assert set(tools["sql_explain"]["inputSchema"]["properties"]) == {"sql"}
    plain = mcp.Server(api.Client(base_url="https://n", token=CANARY, session=FakeSession()), sql=False)
    assert "sql_read" not in {t["name"] for t in plain.handle(rpc("tools/list"))["result"]["tools"]}
    assert plain.handle(rpc("tools/call", {"name": "sql_read", "arguments": {"sql": "select 1"}}))["error"]["code"] == -32602


def test_sql_read_runs_a_reading_statement_and_a_write_is_a_tool_error_with_no_request():
    view = {"statementId": "q1", "state": "SUCCEEDED", "columns": [], "rows": [[1]], "rowCount": 1, "truncated": False}
    srv, session = server(FakeResponse(200, view), FakeResponse(204, None))
    reply = call(srv, "sql_read", {"sql": "select 1"})
    assert reply["result"]["isError"] is False and json.loads(text_of(reply))["rows"] == [[1]]
    srv, session = server()
    reply = call(srv, "sql_read", {"sql": "drop table t"})
    assert reply["result"]["isError"] is True and "not DROP" in text_of(reply)
    reply = call(srv, "sql_explain", {"sql": "select 1", "maxRows": 5})
    assert reply["result"]["isError"] is True and "unexpected arguments: maxRows" in text_of(reply)
    assert session.calls == []


def test_the_log_line_of_a_sql_call_never_holds_the_statement():
    logged = []
    session = FakeSession()
    srv = mcp.Server(api.Client(base_url="https://n", token=CANARY, session=session), log=lambda n, e: logged.append((n, e)))
    srv.handle(rpc("tools/call", {"name": "sql_read", "arguments": {"sql": "drop table secret_table"}}))
    assert logged == [("sql_read", True)]


def test_the_command_can_leave_the_sql_tools_out():
    lines = json.dumps(rpc("tools/list")) + "\n"
    code, out, _ = run_cli(["mcp", "serve"], lines)
    assert code == 0 and "sql_read" in out
    code, out, _ = run_cli(["mcp", "serve", "--no-sql"], lines)
    assert code == 0 and "sql_read" not in out
