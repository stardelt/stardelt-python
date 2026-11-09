"""``stardelt.sql`` from a laptop kernel: Nova's statement API with the stored sign-in.

No network: ``stardelt.statements.run`` is faked, the sign-in directory points at an empty
temporary XDG_CONFIG_HOME, and the platform path uses the stub Trino connection from
``tests/test_stardelt.py``.
"""

import pandas as pd
import pytest

import stardelt
import stardelt.api as api
from stardelt import statements
from tests.test_stardelt import StubConnection, _jwt

LOCAL_ENV = ("TRINO_URL", "TRINO_TOKEN", "STARDELT_NOVA_URL", "STARDELT_TOKEN", "PIPELINE_TOKEN_URL", "PIPELINE_CLIENT_SECRET")


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch, tmp_path):
    """No platform, no leftover sign-in: every case starts on a laptop with nothing stored."""
    for name in LOCAL_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setattr(stardelt, "_LOCAL_SESSION", None)
    StubConnection.instances.clear()
    yield


@pytest.fixture
def signed_in(monkeypatch):
    monkeypatch.setenv("STARDELT_NOVA_URL", "https://nova.example")
    monkeypatch.setenv("STARDELT_TOKEN", "test-token")


class FakeRun:
    """Stands in for ``stardelt.statements.run``: records the queries, replays one result."""

    def __init__(self, payload=None):
        self.payload = payload if payload is not None else {
            "columns": [{"name": "a"}],
            "rows": [[1]],
            "row_count": 1,
            "truncated": False,
        }
        self.queries = []
        self.clients = []

    def __call__(self, client, query, *args, **kwargs):
        self.queries.append(query)
        self.clients.append(client)
        return api.QueryResult(self.payload)


def test_sql_returns_a_dataframe_from_the_statement_api(monkeypatch, signed_in):
    run = FakeRun()
    monkeypatch.setattr(statements, "run", run)
    frame = stardelt.sql("SELECT 1 AS a")
    assert isinstance(frame, pd.DataFrame) and frame.shape == (1, 1)
    assert list(frame.columns) == ["a"] and frame.iloc[0, 0] == 1
    assert run.queries == ["SELECT 1 AS a"]


def test_a_truncated_laptop_result_warns(monkeypatch, signed_in):
    run = FakeRun({"columns": [{"name": "a"}], "rows": [[1]], "row_count": 1, "truncated": True})
    monkeypatch.setattr(statements, "run", run)
    with pytest.warns(UserWarning, match="100 000 rows"):
        frame = stardelt.sql("SELECT 1 AS a")
    assert frame.iloc[0, 0] == 1


def test_bound_parameters_are_refused_on_a_laptop(monkeypatch, signed_in):
    run = FakeRun()
    monkeypatch.setattr(statements, "run", run)
    with pytest.raises(RuntimeError, match="bound parameters need the platform connection"):
        stardelt.sql("SELECT ? AS a", params=[1])
    assert run.queries == []


def test_no_host_and_no_sign_in_names_stardelt_login(monkeypatch):
    run = FakeRun()
    monkeypatch.setattr(statements, "run", run)
    with pytest.raises(RuntimeError, match="not on the platform and not signed in"):
        stardelt.sql("SELECT 1")
    assert run.queries == []


def test_spark_and_write_df_are_platform_only(monkeypatch):
    with pytest.raises(RuntimeError, match="stardelt.spark: Spark and table writes run on the platform"):
        stardelt.spark()
    with pytest.raises(RuntimeError, match="stardelt.write_df: Spark and table writes run on the platform"):
        stardelt.write_df(pd.DataFrame({"a": [1]}), "demo.t")


def test_on_the_platform_the_trino_path_is_used(monkeypatch):
    run = FakeRun()
    monkeypatch.setattr(statements, "run", run)
    monkeypatch.setattr(stardelt, "_trino_connect", StubConnection)
    monkeypatch.setenv("TRINO_URL", "https://trino.example:8443")
    monkeypatch.setenv("TRINO_TOKEN", _jwt("analyst"))
    frame = stardelt.sql("SELECT id, note FROM t")
    assert list(frame.columns) == ["id", "note"] and len(frame) == 2
    assert run.queries == []
