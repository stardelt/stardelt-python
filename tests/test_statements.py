"""Long queries through the statement API (stardelt.statements). No network: a fake session stands in for Nova."""

import pytest

import stardelt.api as api
from stardelt import statements
from tests.test_api import CANARY, FakeResponse, FakeSession

COLUMNS = [{"name": "x", "type": "integer"}]
GONE = FakeResponse(204, None)


def nova(*responses):
    session = FakeSession(*responses)
    return api.Client(base_url="https://nova.example", token=CANARY, session=session), session


def view(state="SUCCEEDED", rows=None, **extra):
    body = {"statementId": "q1", "state": state, "columns": COLUMNS, "rowCount": len(rows or []), "chunks": 1, "truncated": False}
    if rows is not None:
        body["rows"] = rows
    return FakeResponse(200, dict(body, **extra))


def sent(session):
    return [(m, u.rsplit("/api", 1)[1]) for m, u, _ in session.calls]


def test_a_short_statement_is_one_submit_and_one_cancel():
    client, session = nova(view(rows=[[1], [2]]), GONE)
    result = statements.run(client, "select x from t")
    assert (result.columns, result.rows, result.row_count, result.truncated) == (COLUMNS, [[1], [2]], 2, False)
    assert sent(session) == [("POST", "/statements"), ("DELETE", "/statements/q1")]
    assert session.calls[0][2]["json"] == {"sql": "select x from t", "waitSeconds": 30}


def test_a_running_statement_is_polled_with_a_wait_until_it_ends():
    client, session = nova(view("RUNNING"), view("RUNNING"), view(rows=[[7]]), GONE)
    assert statements.run(client, "select x from t").rows == [[7]]
    assert [m for m, _ in sent(session)] == ["POST", "GET", "GET", "DELETE"]
    assert session.calls[1][2]["params"] == {"waitSeconds": 10}


def test_the_chunks_after_the_first_are_read_in_order():
    client, session = nova(view(rows=[[1], [2]], chunks=3, rowCount=5), FakeResponse(200, {"rows": [[3], [4]]}), FakeResponse(200, {"rows": [[5]]}), GONE)
    result = statements.run(client, "select x from t")
    assert result.rows == [[1], [2], [3], [4], [5]] and result.row_count == 5
    assert sent(session) == [
        ("POST", "/statements"),
        ("GET", "/statements/q1/chunks/1"),
        ("GET", "/statements/q1/chunks/2"),
        ("DELETE", "/statements/q1"),
    ]


def test_a_statement_with_no_rows_is_an_empty_result():
    client, _ = nova(view(rows=None, chunks=0), GONE)
    result = statements.run(client, "select x from t where false")
    assert (result.rows, result.row_count) == ([], 0)


def test_a_result_nova_cut_is_marked_truncated():
    client, _ = nova(view(rows=[[1]], truncated=True), GONE)
    assert statements.run(client, "select x from t").truncated is True


def test_a_failed_statement_gives_the_engine_sentence_and_still_frees_the_slot():
    failed = view("FAILED", error="SYNTAX_ERROR: line 1:1: mismatched input")
    client, session = nova(failed, GONE)
    with pytest.raises(statements.StatementError, match="SYNTAX_ERROR: line 1:1"):
        statements.run(client, "select x from")
    assert sent(session)[-1] == ("DELETE", "/statements/q1")


def test_a_statement_that_takes_longer_than_max_seconds_is_cancelled_and_says_so():
    ticks = iter([0, 11])
    client, session = nova(view("RUNNING"), GONE)
    with pytest.raises(statements.StatementError, match="did not finish in 10 seconds and was cancelled"):
        statements.run(client, "select x from t", max_seconds=10, clock=lambda: next(ticks))
    assert sent(session)[-1] == ("DELETE", "/statements/q1")


def test_ctrl_c_while_waiting_cancels_the_statement():
    def clock():
        if clock.calls:
            raise KeyboardInterrupt
        clock.calls += 1
        return 0

    clock.calls = 0
    client, session = nova(view("RUNNING"), GONE)
    with pytest.raises(KeyboardInterrupt):
        statements.run(client, "select x from t", max_seconds=60, clock=clock)
    assert sent(session)[-1] == ("DELETE", "/statements/q1")


def test_a_cancel_that_fails_does_not_hide_the_rows():
    client, _ = nova(view(rows=[[1]]), FakeResponse(404, {"error": "no such statement"}))
    assert statements.run(client, "select x from t").rows == [[1]]


def test_the_client_method_runs_the_same_thing():
    client, session = nova(view(rows=[[9]]), GONE)
    assert client.statement("select x from t").rows == [[9]]
    assert sent(session)[0] == ("POST", "/statements")
