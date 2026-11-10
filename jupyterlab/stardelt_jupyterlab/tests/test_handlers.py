"""The server side of the Publish button: path safety, the publish call, its errors.

The catalog route is tested here too: it is the same server extension and the
same signed-in person.
"""

import importlib
import json

import pandas
import pytest
import stardelt
from tornado.httpclient import HTTPClientError


async def test_publish_info_refuses_a_path_outside_the_root(jp_fetch, monkeypatch):
    monkeypatch.setattr(stardelt, "_token", lambda: "not-a-real-token")
    # A notebook inside the workspace root is answered normally, so the 400
    # below is the path check and not everything failing.
    inside = await jp_fetch("stardelt", "publish-info", params={"path": "x.ipynb"})
    assert inside.code == 200

    with pytest.raises(HTTPClientError) as failure:
        await jp_fetch("stardelt", "publish-info", params={"path": "../x.ipynb"})

    assert failure.value.code == 400


async def test_publish_answers_what_the_library_answered(jp_fetch, monkeypatch):
    module = importlib.import_module("stardelt.publish")
    calls = {}

    def fake_publish(path, group=None, name=None):
        calls.update(path=path, group=group, name=name)
        return {
            "name": "a.ipynb",
            "group": "analytics",
            "version": "abc123",
            "sha256": "deadbeef",
            "parameters": [],
        }

    monkeypatch.setattr(module, "publish", fake_publish)
    monkeypatch.setattr(stardelt, "_token", lambda: "not-a-real-token")

    response = await jp_fetch(
        "stardelt",
        "publish",
        method="POST",
        body=json.dumps({"path": "a.ipynb", "group": "analytics", "name": "a.ipynb"}),
    )

    stored = json.loads(response.body)
    assert stored["version"] == "abc123"
    assert stored["group"] == "analytics"
    assert calls["group"] == "analytics"
    assert calls["path"].endswith("a.ipynb")


async def test_publish_turns_a_library_error_into_400(jp_fetch, monkeypatch):
    module = importlib.import_module("stardelt.publish")

    def failing_publish(path, group=None, name=None):
        raise RuntimeError("no")

    monkeypatch.setattr(module, "publish", failing_publish)

    with pytest.raises(HTTPClientError) as failure:
        await jp_fetch("stardelt", "publish", method="POST", body=json.dumps({"path": "a.ipynb"}))

    assert failure.value.code == 400
    assert json.loads(failure.value.response.body) == {"error": "no"}


async def test_publish_info_turns_a_library_error_into_400(jp_fetch, monkeypatch):
    # A server without its spawn token: the library raises, the dialog must get
    # the sentence and a 400, not Tornado's generic 500 "Unhandled error".
    def no_token():
        raise RuntimeError("TRINO_TOKEN is not set — restart this server from the hub")

    monkeypatch.setattr(stardelt, "_token", no_token)

    with pytest.raises(HTTPClientError) as failure:
        await jp_fetch("stardelt", "publish-info", params={"path": "a.ipynb"})

    assert failure.value.code == 400
    assert json.loads(failure.value.response.body) == {
        "error": "TRINO_TOKEN is not set — restart this server from the hub"
    }


async def test_catalog_hides_engine_catalogs(jp_fetch, monkeypatch):
    monkeypatch.setattr(
        stardelt,
        "sql",
        lambda statement: pandas.DataFrame({"Catalog": ["mobility", "system"]}),
    )

    response = await jp_fetch("stardelt", "catalog")

    assert response.code == 200
    assert json.loads(response.body) == {"catalogs": ["mobility"]}


async def test_catalog_refuses_a_non_name(jp_fetch):
    with pytest.raises(HTTPClientError) as failure:
        await jp_fetch("stardelt", "catalog", params={"catalog": 'evil"'})

    assert failure.value.code == 400


async def test_catalog_answers_a_warehouse_error_as_502(jp_fetch, monkeypatch):
    def no_token(statement):
        raise RuntimeError("no token")

    monkeypatch.setattr(stardelt, "sql", no_token)

    with pytest.raises(HTTPClientError) as failure:
        await jp_fetch("stardelt", "catalog")

    assert failure.value.code == 502
    assert json.loads(failure.value.response.body) == {"error": "no token"}
