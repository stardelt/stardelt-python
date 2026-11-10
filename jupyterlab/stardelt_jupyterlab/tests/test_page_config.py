import os

from stardelt_jupyterlab import _load_jupyter_server_extension


class FakeWebApp:
    def __init__(self):
        self.settings = {"base_url": "/"}
        self.handlers = []

    def add_handlers(self, host_pattern, host_handlers):
        self.handlers.append((host_pattern, list(host_handlers)))


class FakeServerApp:
    def __init__(self):
        self.web_app = FakeWebApp()
        self.root_dir = "/workspace"
        self.log = _FakeLog()


class _FakeLog:
    def info(self, message):
        pass


def test_page_config_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("STARDELT_NOVA_URL", "https://alpha.dev.stardelt.io")
    monkeypatch.setenv("STARDELT_RUNTIME", "2026.11")
    server_app = FakeServerApp()

    _load_jupyter_server_extension(server_app)

    page_config = server_app.web_app.settings["page_config_data"]
    assert page_config["stardeltNovaUrl"] == "https://alpha.dev.stardelt.io"
    assert page_config["stardeltRuntime"] == "2026.11"


def test_page_config_is_empty_without_the_environment(monkeypatch):
    monkeypatch.delenv("STARDELT_NOVA_URL", raising=False)
    monkeypatch.delenv("STARDELT_RUNTIME", raising=False)
    server_app = FakeServerApp()

    _load_jupyter_server_extension(server_app)

    page_config = server_app.web_app.settings["page_config_data"]
    assert page_config["stardeltNovaUrl"] == ""
    assert page_config["stardeltRuntime"] == ""


def test_page_config_keeps_what_the_server_already_set(monkeypatch):
    monkeypatch.setenv("STARDELT_RUNTIME", "2026.11")
    server_app = FakeServerApp()
    server_app.web_app.settings["page_config_data"] = {"existing": "kept"}

    _load_jupyter_server_extension(server_app)

    page_config = server_app.web_app.settings["page_config_data"]
    assert page_config["existing"] == "kept"
    assert page_config["stardeltRuntime"] == "2026.11"


def test_the_publish_routes_are_registered():
    server_app = FakeServerApp()

    _load_jupyter_server_extension(server_app)

    paths = [path for _host, routes in server_app.web_app.handlers for path, _handler in routes]
    assert paths == ["/stardelt/publish-info", "/stardelt/publish", "/stardelt/catalog"]
    assert server_app.web_app.settings["stardelt_root_dir"] == "/workspace"
