"""Tests for ``stardelt.publish`` (stardelt/publish.py).

A fake Nova (one PUT route on a local HTTP server) stands in for the real
service: it records the request and answers the shape backend/src/notebooks.rs
returns. What it cannot prove — the real object store, the group policy and the
public TLS hop — the dev-cluster check in platform#291 covers.

The token canary is asserted absent from every result, print and error: the
helper holds the person's credential and must never leak it into a notebook.
"""

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import stardelt
from stardelt.publish import MAX_PUBLISH_BYTES

CANARY = "canary-token-291"

NOTEBOOK = json.dumps(
    {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {},
        "cells": [
            {
                "cell_type": "code",
                "metadata": {"tags": ["parameters"]},
                "source": ["run_date = \"2026-10-30\""],
                "outputs": [],
                "execution_count": None,
            }
        ],
    }
).encode()


def _jwt(groups=("analytics",)):
    payload = base64.urlsafe_b64encode(
        json.dumps({"sub": "sub-alice", "groups": list(groups)}).encode()
    ).decode()
    return f"header.{payload.rstrip('=')}.{CANARY}"


class FakeNova:
    """Records every PUT and answers with ``reply`` under ``status``."""

    def __init__(self):
        self.requests = []
        self.status = 200
        self.reply = {
            "name": "demo.ipynb",
            "group": "analytics",
            "objectVersion": "v-3",
            "sha256": "d1e2f3",
            "size": len(NOTEBOOK),
            "parameters": [{"name": "run_date", "default": "2026-10-30"}],
        }

        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silence
                pass

            def do_PUT(self):
                length = int(self.headers.get("Content-Length", 0))
                fake.requests.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "authorization": self.headers.get("Authorization"),
                        "content_type": self.headers.get("Content-Type"),
                        "body": self.rfile.read(length),
                    }
                )
                raw = json.dumps(fake.reply).encode()
                self.send_response(fake.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def nova(monkeypatch):
    fake = FakeNova()
    monkeypatch.setenv("STARDELT_NOVA_URL", fake.url)
    monkeypatch.setenv("TRINO_TOKEN", _jwt())
    monkeypatch.delenv("PIPELINE_TOKEN_URL", raising=False)
    monkeypatch.delenv("PIPELINE_CLIENT_SECRET", raising=False)
    return fake


@pytest.fixture
def notebook(tmp_path):
    path = tmp_path / "demo.ipynb"
    path.write_bytes(NOTEBOOK)
    return path


def test_publish_sends_bearer_and_body(nova, notebook, capsys):
    result = stardelt.publish(notebook, group="analytics")

    assert result == {
        "name": "demo.ipynb",
        "group": "analytics",
        "version": "v-3",
        "sha256": "d1e2f3",
        "parameters": [{"name": "run_date", "default": "2026-10-30"}],
    }
    assert "'version': 'v-3'" in capsys.readouterr().out

    assert len(nova.requests) == 1
    request = nova.requests[0]
    assert request["method"] == "PUT"
    assert request["path"] == "/api/notebooks/analytics/demo.ipynb"
    assert request["authorization"] == f"Bearer {_jwt()}"
    assert request["content_type"] == "application/json"
    assert request["body"] == NOTEBOOK


def test_group_defaults_to_the_only_group(nova, notebook):
    stardelt.publish(notebook)

    assert nova.requests[0]["path"] == "/api/notebooks/analytics/demo.ipynb"


def test_ambiguous_group_is_an_error(nova, notebook, monkeypatch):
    monkeypatch.setenv("TRINO_TOKEN", _jwt(("analytics", "finance")))

    with pytest.raises(ValueError) as exc:
        stardelt.publish(notebook)

    message = str(exc.value)
    assert "analytics" in message and "finance" in message
    assert nova.requests == []


def test_server_error_is_one_line(nova, notebook):
    nova.status = 403
    nova.reply = {"error": "you are not a member of the group finance"}

    with pytest.raises(RuntimeError) as exc:
        stardelt.publish(notebook, group="finance")

    assert str(exc.value) == (
        "stardelt.publish failed: you are not a member of the group finance"
    )
    assert "\n" not in str(exc.value)


def test_large_file_refused_locally(nova, tmp_path):
    path = tmp_path / "big.ipynb"
    path.write_bytes(b"x" * (MAX_PUBLISH_BYTES + 1))

    with pytest.raises(ValueError) as exc:
        stardelt.publish(path)

    assert "5 MiB" in str(exc.value)
    assert nova.requests == []


def test_no_token_in_repr_or_errors(nova, notebook, capsys):
    result = stardelt.publish(notebook)
    printed = capsys.readouterr().out
    assert CANARY not in repr(result)
    assert CANARY not in printed
    assert nova.requests[0]["authorization"] == f"Bearer {_jwt()}"

    # Worst case: something between the pod and Nova echoes the header back.
    nova.status = 500
    nova.reply = {"error": f"upstream said Bearer {_jwt()}"}
    with pytest.raises(RuntimeError) as exc:
        stardelt.publish(notebook)
    assert CANARY not in str(exc.value)
    assert "<token>" in str(exc.value)
