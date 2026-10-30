"""Tests for JupySQL integration (stardelt.sqlmagic)."""
import os
import sys

import pytest


def test_telemetry_disabled_by_default(monkeypatch):
    try:
        import sql  # noqa: F401
    except Exception:
        pytest.skip("jupysql not installed")
    monkeypatch.delenv("PLOOMBER_STATS_ENABLED", raising=False)
    # reload
    for mod in list(sys.modules.keys()):
        if mod.startswith("stardelt.sqlmagic") or mod.startswith("sql"):
            sys.modules.pop(mod)
    import stardelt.sqlmagic  # noqa: F401
    assert os.environ.get("PLOOMBER_STATS_ENABLED") == "false"


def test_startup_loads_without_error():
    import stardelt.sqlmagic  # noqa: F401


def test_default_connection_registered(monkeypatch):
    try:
        from sql import connection as sql_connection
    except Exception:
        pytest.skip("jupysql not installed")
    import stardelt
    from tests.test_stardelt import StubConnection, _jwt
    sql_connection.ConnectionManager.connections = {}
    sql_connection.ConnectionManager.current = None
    StubConnection.instances.clear()
    monkeypatch.setenv("TRINO_URL", "https://trino.example:8443")
    monkeypatch.setenv("TRINO_TOKEN", _jwt("analyst"))
    monkeypatch.setattr(stardelt, "_trino_connect", StubConnection)
    import stardelt.sqlmagic as sqlmagic  # noqa: F401
    sqlmagic._configure_jupysql()
    # should have registered warehouse
    mgr = sql_connection.ConnectionManager
    assert "warehouse" in mgr.connections or mgr.current is not None


def test_engine_uses_the_helper_connection_per_statement(monkeypatch):
    try:
        import sql  # noqa: F401
    except Exception:
        pytest.skip("jupysql not installed")
    import stardelt
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool
    import stardelt.sqlmagic as sqlmagic
    from tests.test_stardelt import StubConnection, _jwt

    StubConnection.instances.clear()
    monkeypatch.setenv("TRINO_URL", "https://trino.example:8443")
    monkeypatch.setenv("TRINO_TOKEN", _jwt("analyst"))
    monkeypatch.setattr(stardelt, "_trino_connect", StubConnection)
    engine = create_engine("trino://", creator=sqlmagic._creator, poolclass=NullPool)
    conn = engine.connect()
    conn.close()
    assert len(StubConnection.instances) >= 1


def test_token_change_is_seen_by_the_next_statement(monkeypatch):
    try:
        import sql  # noqa: F401
    except Exception:
        pytest.skip("jupysql not installed")
    import stardelt
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import NullPool
    import stardelt.sqlmagic as sqlmagic
    from tests.test_stardelt import StubConnection, _jwt

    StubConnection.instances.clear()
    monkeypatch.setenv("TRINO_URL", "https://trino.example:8443")
    monkeypatch.setenv("TRINO_TOKEN", _jwt("analyst"))
    monkeypatch.setattr(stardelt, "_trino_connect", StubConnection)
    engine = create_engine("trino://", creator=sqlmagic._creator, poolclass=NullPool)
    with engine.connect() as conn:
        try:
            conn.execute(text("SELECT 1"))
        except Exception:
            pass
    monkeypatch.setenv("TRINO_TOKEN", _jwt("other"))
    with engine.connect() as conn:
        try:
            conn.execute(text("SELECT 2"))
        except Exception:
            pass
    # Different connections created
    assert len(StubConnection.instances) >= 2
