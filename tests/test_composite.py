"""The composite MCP tools catalog_tree and describe_table (stardelt.composite). No network: a fake session stands in."""

import json

from stardelt import composite, mcp
from tests.test_api import CANARY, FakeResponse
from tests.test_mcp import call, rpc, server, text_of

NAMESPACES = {"namespaces": [["raw"], ["demo"]]}


def tables(*names):
    return FakeResponse(payload={"identifiers": [{"namespace": ["x"], "name": name} for name in names]})


def urls(session):
    return [url.removeprefix("https://nova.example") for _, url, _ in session.calls]


def test_both_tools_are_offered_read_only_and_only_when_the_operations_they_need_are():
    srv, _ = server()
    tools = {t["name"]: t for t in srv.handle(rpc("tools/list"))["result"]["tools"]}
    assert tools["catalog_tree"]["annotations"]["readOnlyHint"] is True
    assert tools["describe_table"]["inputSchema"]["required"] == ["namespace", "table"]
    only_me, _ = server(allowed=("me", "tables_list"))
    names = {t["name"] for t in only_me.handle(rpc("tools/list"))["result"]["tools"]}
    assert not names & {"catalog_tree", "describe_table"}
    assert only_me.handle(rpc("tools/call", {"name": "catalog_tree", "arguments": {}}))["error"]["code"] == -32602


def test_catalog_tree_lists_every_namespace_with_its_sorted_tables_in_one_answer():
    srv, session = server(FakeResponse(payload=NAMESPACES), tables("b", "a"), tables("trips"))
    reply = call(srv, "catalog_tree", {"catalog": "main"})
    assert reply["result"]["isError"] is False
    assert json.loads(text_of(reply)) == {
        "namespaces": [{"namespace": "raw", "tables": ["a", "b"]}, {"namespace": "demo", "tables": ["trips"]}],
        "catalog": "main",
    }
    assert urls(session) == [
        "/api/catalog/namespaces",
        "/api/catalog/namespaces/raw/tables",
        "/api/catalog/namespaces/demo/tables",
    ]
    assert all(kwargs["params"] == {"catalog": "main"} for _, _, kwargs in session.calls)


def test_one_namespace_can_be_asked_for_and_an_unknown_one_is_a_tool_error():
    srv, session = server(FakeResponse(payload=NAMESPACES), tables("trips"))
    assert json.loads(text_of(call(srv, "catalog_tree", {"namespace": "demo"})))["namespaces"] == [
        {"namespace": "demo", "tables": ["trips"]}
    ]
    assert len(session.calls) == 2
    srv, session = server(FakeResponse(payload=NAMESPACES))
    reply = call(srv, "catalog_tree", {"namespace": "nope"})
    assert reply["result"]["isError"] is True and text_of(reply) == "no namespace nope"
    assert len(session.calls) == 1


def test_the_answer_is_bounded_and_says_what_it_left_out():
    many = {"namespaces": [[f"n{i:02d}"] for i in range(composite.MAX_NAMESPACES + 1)]}
    answers = [tables(*[f"t{i}" for i in range(composite.MAX_TABLES + 1)])] * composite.MAX_NAMESPACES
    srv, session = server(FakeResponse(payload=many), *answers)
    answer = json.loads(text_of(call(srv, "catalog_tree")))
    assert len(answer["namespaces"]) == composite.MAX_NAMESPACES and answer["namespacesOmitted"] == 1
    first, third, last = answer["namespaces"][0], answer["namespaces"][2], answer["namespaces"][-1]
    assert len(first["tables"]) == composite.MAX_TABLES and first["tablesOmitted"] == 1
    assert len(third["tables"]) == 100 and third["tablesOmitted"] == composite.MAX_TABLES + 1 - 100
    assert last["tables"] == [] and last["tablesOmitted"] == composite.MAX_TABLES + 1
    assert sum(len(n["tables"]) for n in answer["namespaces"]) == composite.MAX_TOTAL_TABLES
    assert len(session.calls) == 1 + composite.MAX_NAMESPACES, "the 51st namespace is never asked about"


def test_a_namespace_that_cannot_be_read_is_named_with_nova_s_sentence_and_the_rest_is_still_listed():
    srv, _ = server(
        FakeResponse(payload=NAMESPACES),
        FakeResponse(status=403, payload={"error": "you may not browse raw"}),
        tables("trips"),
    )
    answer = json.loads(text_of(call(srv, "catalog_tree")))
    assert answer["namespaces"] == [
        {"namespace": "raw", "error": "you may not browse raw (HTTP 403)"},
        {"namespace": "demo", "tables": ["trips"]},
    ]


DOCS = {
    "description": "Taxi trips",
    "owner": "analytics",
    "domain": None,
    "tags": ["demo"],
    "columns": [{"name": "id", "type": "bigint", "description": None}],
    "canEdit": True,
    "editRule": "you are in the owner group",
}
METADATA = {
    "metadata-location": "s3://warehouse/demo/trips/metadata/1.json",
    "metadata": {
        "location": "s3://warehouse/demo/trips",
        "default-spec-id": 1,
        "partition-specs": [
            {"spec-id": 0, "fields": []},
            {"spec-id": 1, "fields": [{"name": "day", "transform": "day", "source-id": 3}]},
        ],
    },
}


def history(count):
    return {
        "columns": [{"name": "snapshot_id", "type": "varchar"}, {"name": "operation", "type": "varchar"}],
        "rows": [[str(i), "append"] for i in range(count)],
        "row_count": count,
        "truncated": False,
    }


def test_describe_table_answers_with_the_documentation_the_location_the_partitioning_and_the_last_snapshots():
    srv, session = server(FakeResponse(payload=DOCS), FakeResponse(payload=METADATA), FakeResponse(payload=history(7)))
    answer = json.loads(text_of(call(srv, "describe_table", {"namespace": "demo", "table": "trips"})))
    assert answer["table"] == "demo.trips" and answer["description"] == "Taxi trips" and answer["owner"] == "analytics"
    assert answer["columns"] == [{"name": "id", "type": "bigint", "description": None}]
    assert answer["location"] == "s3://warehouse/demo/trips"
    assert answer["partitioning"] == [{"name": "day", "transform": "day"}]
    assert answer["recentSnapshots"] == [{"snapshot_id": str(i), "operation": "append"} for i in range(composite.RECENT_SNAPSHOTS)]
    assert "canEdit" not in answer and CANARY not in json.dumps(answer)
    assert urls(session) == [
        "/api/catalog/tables/demo/trips/documentation",
        "/api/catalog/tables/demo/trips",
        "/api/catalog/tables/demo/trips/history",
    ]


def test_a_part_that_cannot_be_read_is_named_and_the_rest_is_still_returned():
    srv, _ = server(
        FakeResponse(payload=DOCS),
        FakeResponse(status=502, payload={"error": "the catalog is not reachable"}),
        FakeResponse(status=502, payload={"error": "the engine is not reachable"}),
    )
    reply = call(srv, "describe_table", {"namespace": "demo", "table": "trips"})
    assert reply["result"]["isError"] is False
    answer = json.loads(text_of(reply))
    assert answer["metadataError"] == "the catalog is not reachable (HTTP 502)" and "location" not in answer
    assert answer["recentSnapshotsError"] == "the engine is not reachable (HTTP 502)" and answer["description"] == "Taxi trips"


def test_a_table_that_does_not_exist_is_a_tool_error_and_wrong_arguments_make_no_request():
    srv, session = server(FakeResponse(status=404, payload={"error": "no such table"}))
    reply = call(srv, "describe_table", {"namespace": "demo", "table": "nope"})
    assert reply["result"]["isError"] is True and text_of(reply) == "no such table (HTTP 404)"
    srv, session = server()
    assert call(srv, "describe_table", {"namespace": "demo"})["result"]["isError"] is True
    assert call(srv, "describe_table", {"namespace": "demo", "table": "t", "extra": 1})["result"]["isError"] is True
    assert call(srv, "catalog_tree", {"nope": 1})["result"]["isError"] is True
    assert session.calls == []
