"""Guarded SQL for agents (stardelt.guarded_sql). No network: a fake session stands in for Nova."""

import json

import pytest

import stardelt.api as api
from stardelt import guarded_sql as g
from tests.test_api import CANARY, FakeResponse, FakeSession

COLUMNS = [{"name": "x", "type": "integer"}]


def nova(*responses):
    session = FakeSession(*responses)
    return api.Client(base_url="https://nova.example", token=CANARY, session=session), session


def done(rows, **extra):
    view = {"statementId": "q1", "state": "SUCCEEDED", "columns": COLUMNS, "rows": rows, "rowCount": len(rows), "truncated": False}
    return FakeResponse(200, dict(view, **extra))


GONE = FakeResponse(204, None)


@pytest.mark.parametrize(
    "sql, sent",
    [
        ("select 1", "select 1"),
        ("  /* a comment */ SELECT 1 -- and another", "SELECT 1"),
        ("select 1;", "select 1"),
        ("select ';' as semicolon", "select ';' as semicolon"),
        ("select 'it''s; fine'", "select 'it''s; fine'"),
        ("with a as (select 1 as x) select * from a", "with a as (select 1 as x) select * from a"),
        ("(select 1) union all (select 2)", "(select 1) union all (select 2)"),
        ("show tables", "show tables"),
        ("describe warehouse.demo.trips", "describe warehouse.demo.trips"),
        ("explain select 1", "explain select 1"),
    ],
)
def test_a_reading_statement_is_sent_without_its_comments(sql, sent):
    assert g.classify(sql) == sent


@pytest.mark.parametrize(
    "sql, why",
    [
        ("drop table t", "not DROP"),
        ("delete from t", "not DELETE"),
        ("insert into t values (1)", "not INSERT"),
        ("select 1; select 2", "exactly one statement"),
        ("select 1; drop table t", "exactly one statement"),
        ("/* select */ drop table t", "not DROP"),
        ("explain analyze select 1", "EXPLAIN ANALYZE"),
        ("   ", "empty"),
        (None, "empty"),
    ],
)
def test_anything_else_is_refused_with_one_sentence(sql, why):
    with pytest.raises(g.Refused, match=why):
        g.classify(sql)


def test_sql_explain_puts_explain_in_front_once():
    assert g.classify("select 1", explain=True) == "EXPLAIN select 1"
    assert g.classify("explain select 1", explain=True) == "explain select 1"


def test_a_short_statement_is_one_submit_and_one_cancel():
    client, session = nova(done([[1], [2], [3]]), GONE)
    answer = json.loads(g.run(client, "select x from t"))
    assert answer == {"columns": COLUMNS, "rows": [[1], [2], [3]], "rowCount": 3, "truncated": False}
    assert [(m, u.rsplit("/api", 1)[1]) for m, u, _ in session.calls] == [("POST", "/statements"), ("DELETE", "/statements/q1")]
    assert session.calls[0][2]["json"] == {"sql": "select x from t", "waitSeconds": 30}


def test_more_rows_than_asked_for_are_cut_and_the_hint_says_what_to_do():
    client, _ = nova(done([[i] for i in range(5)]), GONE)
    answer = json.loads(g.run(client, "select x from t", max_rows=2))
    assert answer["rows"] == [[0], [1]] and answer["rowCount"] == 2 and answer["truncated"] is True
    assert "aggregate or add a LIMIT" in answer["hint"]


def test_a_statement_the_engine_cut_is_marked_truncated_even_when_all_chunk_rows_fit():
    client, _ = nova(done([[1]], truncated=True), GONE)
    assert json.loads(g.run(client, "select x from t"))["truncated"] is True


def test_a_running_statement_is_polled_with_a_wait_until_it_ends():
    running = {"statementId": "q1", "state": "RUNNING", "columns": [], "rowCount": 0, "truncated": False}
    client, session = nova(FakeResponse(202, running), FakeResponse(200, running), done([[7]]), GONE)
    assert json.loads(g.run(client, "select x from t"))["rows"] == [[7]]
    assert [m for m, _, _ in session.calls] == ["POST", "GET", "GET", "DELETE"]
    assert session.calls[1][2]["params"] == {"waitSeconds": 10}


def test_a_statement_that_takes_too_long_is_cancelled_and_says_so():
    running = {"statementId": "q1", "state": "RUNNING", "columns": [], "rowCount": 0, "truncated": False}
    ticks = iter([0, g.TIMEOUT_SECONDS + 1])
    client, session = nova(FakeResponse(202, running), GONE)
    with pytest.raises(g.Refused, match="did not finish in 60 seconds and was cancelled"):
        g.run(client, "select x from t", clock=lambda: next(ticks))
    assert session.calls[-1][0] == "DELETE"


def test_a_failed_statement_gives_the_engine_sentence_and_still_frees_the_slot():
    failed = {"statementId": "q1", "state": "FAILED", "error": "SYNTAX_ERROR: line 1:1: mismatched input", "columns": [], "rowCount": 0}
    client, session = nova(FakeResponse(200, failed), GONE)
    with pytest.raises(g.Refused, match="SYNTAX_ERROR: line 1:1"):
        g.run(client, "select x from")
    assert [m for m, _, _ in session.calls] == ["POST", "DELETE"]


def test_a_refused_statement_or_a_bad_row_limit_makes_no_request():
    client, session = nova()
    for sql, rows in (("drop table t", None), ("select 1", 0), ("select 1", g.MAX_ROWS + 1), ("select 1", "5"), ("select 1", True)):
        with pytest.raises(g.Refused):
            g.run(client, sql, max_rows=rows)
    assert session.calls == []
