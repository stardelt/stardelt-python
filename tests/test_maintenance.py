"""`stardelt maintenance run`: one catalog's Iceberg maintenance through Trino (operator#476)."""

import io
import json

from stardelt import _quote_ident, maintenance


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self._rows = ()

    def execute(self, statement):
        self.connection.statements.append(statement)
        answer = self.connection.answer(statement)
        if isinstance(answer, Exception):
            raise answer
        self._rows = () if answer is None else answer

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def close(self):
        pass


class FakeConnection:
    def __init__(self, tables, counts=None, errors=None):
        self.tables = tables
        self.counts = counts or {}
        self.errors = errors or {}
        self.statements = []
        self.closed = False

    def answer(self, statement):
        if statement == maintenance._tables("warehouse"):
            return self.tables
        for table, error in self.errors.items():
            if _quote_ident(table + "$files") in statement:
                return error
        for table, count in self.counts.items():
            if _quote_ident(table + "$files") in statement:
                return [(count,)]
        return None

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.closed = True


class Connector:
    def __init__(self, connection):
        self.connection = connection
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.connection


def run(connection, policy=None):
    connector = Connector(connection)
    out = io.StringIO()
    code = maintenance.run("warehouse", policy if policy is not None else {}, connect=connector, out=out)
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    return code, lines, connector


def test_many_small_files_optimizes_and_cleans_up():
    conn = FakeConnection([("demo", "trips")], counts={"trips": 25})
    code, lines, connector = run(conn)
    assert code == 0
    assert connector.calls == [{"live": True}]
    table, summary = lines
    assert table["table"] == "demo.trips"
    assert table["smallFiles"] == 25
    assert table["optimized"] is True
    assert table["error"] is None
    assert isinstance(table["seconds"], float)
    assert any("EXECUTE optimize(" in s for s in conn.statements)
    assert any("EXECUTE expire_snapshots(" in s for s in conn.statements)
    assert any("EXECUTE remove_orphan_files(" in s for s in conn.statements)
    assert summary == {"catalog": "warehouse", "tablesDone": 1, "tablesFailed": 0}
    assert conn.closed is True


def test_few_small_files_skips_optimize_but_still_expires_and_removes():
    conn = FakeConnection([("demo", "trips")], counts={"trips": 3})
    code, lines, _ = run(conn)
    assert code == 0
    assert lines[0]["smallFiles"] == 3
    assert lines[0]["optimized"] is False
    assert not any("EXECUTE optimize(" in s for s in conn.statements)
    assert any("EXECUTE expire_snapshots(" in s for s in conn.statements)
    assert any("EXECUTE remove_orphan_files(" in s for s in conn.statements)


def test_excluded_table_gets_no_statement():
    conn = FakeConnection([("demo", "trips"), ("demo", "kept")], counts={"trips": 25, "kept": 25})
    code, lines, _ = run(conn, policy={"excludeTables": ["demo.trips"]})
    assert code == 0
    assert conn.statements[0] == maintenance._tables("warehouse")
    assert not any("trips" in statement for statement in conn.statements[1:])
    assert [line.get("table") for line in lines] == ["demo.kept", None]


def test_the_engine_schemas_are_not_maintained():
    sql = maintenance._tables("warehouse")
    assert "'information_schema'" in sql
    assert "'system'" in sql


def test_one_failing_table_does_not_stop_the_run():
    conn = FakeConnection(
        [("demo", "first"), ("demo", "second")],
        counts={"first": 25},
        errors={"second": RuntimeError("boom\nstack")},
    )
    code, lines, _ = run(conn)
    assert code == 1
    first, second, summary = lines
    assert first["table"] == "demo.first"
    assert first["error"] is None
    assert second["table"] == "demo.second"
    assert second["error"] == "boom"
    assert summary == {"catalog": "warehouse", "tablesDone": 1, "tablesFailed": 1}


def test_bytes_reads_powers_of_1024():
    assert maintenance._bytes("128MB") == 134217728


def test_a_table_name_with_a_quote_is_quoted():
    conn = FakeConnection([("demo", 'weird"name')], counts={})
    run(conn)
    assert any('"weird""name$files"' in statement for statement in conn.statements)
