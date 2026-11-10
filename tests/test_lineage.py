"""Tests for stardelt.lineage: the table reference, the rows and the request. No network."""

import pytest

import stardelt.api as api
import stardelt.lineage as lineage
from tests.test_api import FakeResponse, FakeSession


def client(payload):
    session = FakeSession(FakeResponse(payload=payload))
    return api.Client(base_url="https://nova.example", token="t", session=session), session


GRAPH = {
    "nodes": [
        {"id": "mobility.silver|yellow_trips", "namespace": "mobility.silver", "name": "yellow_trips", "visible": True, "level": -1},
        {"id": "mobility.gold|kpis", "namespace": "mobility.gold", "name": "kpis", "visible": True, "level": 0, "lastWrite": "2026-11-09T22:40:00Z", "readers": {"queries": 3, "people": 2, "lastRead": "2026-11-09T23:00:00Z"}},
        {"id": "shop.gold|city", "namespace": "shop.gold", "name": "city", "visible": True, "level": 1, "label": "a dataset you cannot see"},
    ],
    "edges": [
        {"from": "mobility.silver|yellow_trips", "to": "mobility.gold|kpis", "jobNamespace": "mobility", "jobName": "kpis_daily", "runId": "r1", "eventTime": "2026-11-09T22:40:00Z"},
    ],
    "jobs": [{"id": "pipeline|mobility|kpis_daily", "kind": "pipeline", "name": "mobility/kpis_daily", "runs": 3, "lastRun": "2026-11-09T22:40:00Z"}],
}


def test_table_ref_splits_the_last_dot_for_the_table_and_the_first_for_the_catalog():
    assert lineage.table_ref("mobility.gold.kpis") == ("mobility", "gold", "kpis")
    assert lineage.table_ref("warehouse.team.gold.kpis") == ("warehouse", "team.gold", "kpis")
    with pytest.raises(lineage.TableRefError):
        lineage.table_ref("kpis")


def test_graph_asks_lineage_graph_with_the_window():
    c, session = client(GRAPH)
    lineage.graph(c, "mobility.gold.kpis", depth=3, days=7)
    method, url, kwargs = session.calls[0]
    assert method == "GET"
    # api.Client sends the query through `kwargs["params"]`; the URL carries no "?" (api.py `call`).
    assert url == "https://nova.example/api/lineage"
    assert kwargs["params"] == {"catalog": "mobility", "schema": "gold", "table": "kpis", "depth": 3, "days": 7}


def test_rows_name_the_job_that_wrote_a_node_and_count_readers():
    found = lineage.rows(GRAPH)
    assert [row["level"] for row in found] == [-1, 0, 1]
    assert found[0]["dataset"] == "mobility.silver.yellow_trips"
    # The fixture's only edge writes mobility.gold.kpis, so the upstream node has no writer.
    assert found[0]["job"] == ""
    assert found[1]["job"] == "mobility/kpis_daily"
    assert found[1]["readers"] == "2 people, 3 queries"
    assert found[1]["lastWrite"] == "2026-11-09T22:40:00Z"


def test_direction_filters_to_one_side():
    assert [row["dataset"] for row in lineage.rows(GRAPH, "up")] == ["mobility.silver.yellow_trips"]
    assert [row["dataset"] for row in lineage.rows(GRAPH, "down")] == ["shop.gold.city"]


def test_jobs_are_one_line_each():
    assert lineage.jobs(GRAPH) == ["pipeline mobility/kpis_daily  3 runs, last 2026-11-09T22:40:00Z"]
