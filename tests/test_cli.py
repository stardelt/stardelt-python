"""Tests for the `stardelt` command (stardelt.cli). No network: a fake session stands in."""

import io
import json
import subprocess
import sys
import tomllib
from pathlib import Path

import requests

from stardelt import cli
from tests.test_api import CANARY, RESULT, FakeResponse, FakeSession

ENV = {"STARDELT_NOVA_URL": "https://nova.example", "STARDELT_TOKEN": CANARY}


def run(argv, *responses, environ=ENV):
    out, err = io.StringIO(), io.StringIO()
    session = FakeSession(*responses)
    code = cli.main(argv, environ=environ, stdout=out, stderr=err, session=session)
    return code, out.getvalue(), err.getvalue(), session


def test_whoami_prints_the_answer_as_json_with_the_bearer_token():
    code, out, err, session = run(["whoami"], FakeResponse(payload={"user": "analyst"}))
    assert (code, json.loads(out), err) == (0, {"user": "analyst"}, "")
    method, url, kwargs = session.calls[0]
    assert (method, url) == ("GET", "https://nova.example/api/me")
    assert kwargs["headers"] == {"Authorization": f"Bearer {CANARY}"}


def test_sql_prints_an_aligned_table_by_default():
    code, out, _, session = run(["sql", "select x, y"], FakeResponse(payload=RESULT))
    assert code == 0
    assert out == "x  y\n1  a\n2  b\n"
    assert session.calls[0][2]["json"] == {"sql": "select x, y"}


def test_sql_json_is_one_object_per_row_keyed_by_column():
    code, out, _, _ = run(["sql", "q", "--format", "json"], FakeResponse(payload=RESULT))
    assert code == 0
    assert json.loads(out) == [{"x": 1, "y": "a"}, {"x": 2, "y": "b"}]


def test_sql_csv_has_a_header_row():
    code, out, _, _ = run(["sql", "q", "--format", "csv"], FakeResponse(payload=RESULT))
    assert (code, out) == (0, "x,y\n1,a\n2,b\n")


def test_sql_long_runs_the_query_as_a_statement_and_prints_the_same_table():
    done = {"statementId": "q1", "state": "SUCCEEDED", "columns": RESULT["columns"], "rows": RESULT["rows"], "rowCount": 2, "chunks": 1, "truncated": False}
    code, out, _, session = run(["sql", "select x, y", "--long"], FakeResponse(200, done), FakeResponse(204, None))
    assert (code, out) == (0, "x  y\n1  a\n2  b\n")
    assert [(m, u.rsplit("/api", 1)[1]) for m, u, _ in session.calls] == [("POST", "/statements"), ("DELETE", "/statements/q1")]


def test_sql_long_exits_1_with_the_engine_sentence_when_the_statement_fails():
    failed = {"statementId": "q1", "state": "FAILED", "error": "SYNTAX_ERROR: line 1:1", "columns": [], "rowCount": 0, "chunks": 0, "truncated": False}
    code, out, err, _ = run(["sql", "select x from", "--long"], FakeResponse(200, failed), FakeResponse(204, None))
    assert (code, out, err) == (1, "", "stardelt: SYNTAX_ERROR: line 1:1\n")


def test_ctrl_c_during_sql_long_says_interrupted_and_exits_1():
    class Interrupted(FakeSession):
        def request(self, method, url, **kwargs):
            raise KeyboardInterrupt

    out, err = io.StringIO(), io.StringIO()
    code = cli.main(["sql", "select 1", "--long"], environ=ENV, stdout=out, stderr=err, session=Interrupted())
    assert (code, out.getvalue(), err.getvalue()) == (1, "", "stardelt: interrupted\n")


def test_max_rows_cuts_what_is_printed():
    code, out, _, _ = run(["sql", "q", "--max-rows", "1"], FakeResponse(payload=RESULT))
    assert (code, out) == (0, "x  y\n1  a\n")


def test_a_result_nova_cut_is_said_on_stderr_not_stdout():
    cut = dict(RESULT, truncated=True)
    code, out, err, _ = run(["sql", "q"], FakeResponse(payload=cut))
    assert code == 0 and "Nova cut the result at 2 rows" in err and "Nova cut" not in out


def test_no_host_is_a_usage_error_and_no_token_needs_authentication():
    code, _, err, session = run(["whoami"], environ={"STARDELT_TOKEN": CANARY})
    assert (code, session.calls) == (2, []) and "no host" in err
    code, _, err, session = run(["whoami"], environ={"STARDELT_NOVA_URL": "https://n"})
    assert (code, session.calls) == (3, []) and "STARDELT_TOKEN" in err


def test_the_host_flag_wins_over_the_environment():
    _, _, _, session = run(["--host", "https://other.example", "whoami"], FakeResponse(payload={}))
    assert session.calls[0][1] == "https://other.example/api/me"


def test_a_401_exits_3_and_other_errors_exit_1_with_nova_s_sentence():
    code, _, err, _ = run(["whoami"], FakeResponse(status=401, payload={"error": "sign in again"}))
    assert code == 3 and "authentication needed: sign in again (HTTP 401)" in err
    code, _, err, _ = run(["sql", "bad"], FakeResponse(status=400, payload={"error": "no such table"}))
    assert code == 1 and "no such table (HTTP 400)" in err


def test_an_unreachable_host_exits_1_without_a_traceback():
    class Down:
        def request(self, *args, **kwargs):
            raise requests.ConnectionError("boom")

    out, err = io.StringIO(), io.StringIO()
    code = cli.main(["whoami"], environ=ENV, stdout=out, stderr=err, session=Down())
    assert code == 1 and err.getvalue() == "stardelt: cannot reach https://nova.example\n"


def test_the_token_is_never_printed():
    for responses in (
        [FakeResponse(status=500, payload={"error": "server said no"})],
        [FakeResponse(status=401, text="denied")],
    ):
        _, out, err, _ = run(["whoami"], *responses)
        assert CANARY not in out + err


def test_wrong_usage_exits_2_on_stderr():
    for argv in ([], ["nosuch"], ["sql"], ["sql", "q", "--format", "xml"]):
        code, out, err, _ = run(argv)
        assert (code, out) == (2, "") and err.startswith("stardelt: ")


def test_the_console_script_is_declared_and_python_dash_m_works():
    pyproject = tomllib.loads((Path(__file__).parent.parent / "pyproject.toml").read_text())
    assert pyproject["project"]["scripts"] == {"stardelt": "stardelt.cli:main"}
    done = subprocess.run(
        [sys.executable, "-m", "stardelt", "--help"], capture_output=True, text=True, timeout=120
    )
    assert done.returncode == 0 and "usage: stardelt" in done.stdout
