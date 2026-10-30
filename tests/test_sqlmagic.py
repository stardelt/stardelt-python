"""The JupySQL integration (stardelt.sqlmagic). Needs the ``jupyter`` extra, as CI installs it:
a test that skips when JupySQL is missing would stay green with the feature gone."""

import os
import tomllib
from pathlib import Path

import pytest
import requests
from IPython.core.interactiveshell import InteractiveShell
from sql.connection import ConnectionManager

import stardelt
import stardelt.sqlmagic as sqlmagic
from tests.test_stardelt import StubConnection, _jwt


@pytest.fixture
def shell(monkeypatch):
    monkeypatch.setenv("TRINO_URL", "https://trino.example:8443")
    monkeypatch.setenv("TRINO_TOKEN", _jwt("analyst"))
    monkeypatch.setattr(stardelt, "_trino_connect", StubConnection)
    StubConnection.instances.clear()
    ConnectionManager.connections = {}
    ConnectionManager.current = None
    return InteractiveShell.instance()


def test_loading_registers_the_warehouse_connection_and_switches_telemetry_off(shell):
    shell.run_line_magic("load_ext", "stardelt.sqlmagic")
    assert list(ConnectionManager.connections) == ["warehouse"]
    assert os.environ["PLOOMBER_STATS_ENABLED"] == "false"


def test_a_kernel_outside_the_platform_gets_a_warning_not_an_error(shell, monkeypatch):
    monkeypatch.delenv("TRINO_URL")
    with pytest.warns(UserWarning, match="no default connection"):
        sqlmagic.load_ipython_extension(shell)
    assert ConnectionManager.connections == {}


def test_every_request_carries_the_current_token(monkeypatch):
    monkeypatch.setenv("TRINO_URL", "https://trino.example:8443")
    session = requests.Session()
    stardelt._LiveAuthentication().set_http_session(session)
    for who in ("analyst", "other"):
        token = _jwt(who)
        monkeypatch.setenv("TRINO_TOKEN", token)
        prepared = requests.Request("GET", "https://trino.example:8443/v1/info").prepare()
        session.auth(prepared)
        assert prepared.headers["Authorization"] == "Bearer " + token


def test_the_live_connection_does_not_fix_the_token_when_it_opens(monkeypatch):
    monkeypatch.setenv("TRINO_URL", "https://trino.example:8443")
    monkeypatch.setenv("TRINO_TOKEN", _jwt("analyst"))
    monkeypatch.setattr(stardelt, "_trino_connect", StubConnection)
    StubConnection.instances.clear()
    stardelt._connect(live=True)
    stardelt._connect()
    live, fixed = [c.kwargs["auth"] for c in StubConnection.instances]
    assert isinstance(live, stardelt._LiveAuthentication)
    assert not isinstance(fixed, stardelt._LiveAuthentication)


def test_the_magics_connection_is_the_live_one(monkeypatch):
    monkeypatch.setenv("TRINO_URL", "https://trino.example:8443")
    monkeypatch.setenv("TRINO_TOKEN", _jwt("analyst"))
    monkeypatch.setattr(stardelt, "_trino_connect", StubConnection)
    StubConnection.instances.clear()
    sqlmagic._creator()
    assert isinstance(StubConnection.instances[0].kwargs["auth"], stardelt._LiveAuthentication)


def test_the_package_version_is_the_one_in_pyproject():
    pyproject = tomllib.loads((Path(__file__).parent.parent / "pyproject.toml").read_text())
    assert stardelt.__version__ == pyproject["project"]["version"]
