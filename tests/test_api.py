"""Tests for stardelt.api (the Nova API client). No network: a fake session stands in."""

import pytest

import stardelt.api as api

CANARY = "canary-token-DO-NOT-LEAK"


class FakeResponse:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


RESULT = {
    "columns": [{"name": "x", "type": "integer"}, {"name": "y", "type": "varchar"}],
    "rows": [[1, "a"], [2, "b"]],
    "row_count": 2,
    "query_id": "q1",
    "stats": None,
    "truncated": False,
}


def client(*responses, token=CANARY):
    session = FakeSession(*responses)
    return api.Client(base_url="https://nova.example/", token=token, session=session), session


def test_query_posts_sql_with_bearer():
    c, session = client(FakeResponse(payload=RESULT))
    result = c.query("select 1")
    method, url, kwargs = session.calls[0]
    assert (method, url) == ("POST", "https://nova.example/api/query")
    assert kwargs["json"] == {"sql": "select 1"}
    assert kwargs["headers"] == {"Authorization": f"Bearer {CANARY}"}
    assert kwargs["timeout"] == api.DEFAULT_TIMEOUT
    assert result.row_count == 2 and result.query_id == "q1" and result.truncated is False


def test_query_result_to_pandas():
    c, _ = client(FakeResponse(payload=RESULT))
    frame = c.query("select 1").to_pandas()
    assert list(frame.columns) == ["x", "y"]
    assert frame.to_dict("records") == [{"x": 1, "y": "a"}, {"x": 2, "y": "b"}]


def test_me_gets_api_me():
    c, session = client(FakeResponse(payload={"user": "analyst"}))
    assert c.me() == {"user": "analyst"}
    assert session.calls[0][:2] == ("GET", "https://nova.example/api/me")


def test_error_envelope_becomes_one_line_error():
    c, _ = client(FakeResponse(status=400, payload={"error": "line one\nline two"}))
    with pytest.raises(api.StardeltApiError) as caught:
        c.query("select")
    assert caught.value.status == 400
    assert str(caught.value) == "line one line two (HTTP 400)"


def test_non_json_error_is_one_line():
    c, _ = client(FakeResponse(status=502, text="  Bad\nGateway  "))
    with pytest.raises(api.StardeltApiError) as caught:
        c.me()
    assert str(caught.value) == "Bad Gateway (HTTP 502)"


def test_missing_base_url_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("STARDELT_NOVA_URL", raising=False)
    with pytest.raises(ValueError, match="STARDELT_NOVA_URL"):
        api.Client(token="t")


def test_base_url_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("STARDELT_NOVA_URL", "https://env.example/")
    assert api.Client(token="t").base_url == "https://env.example"


def test_token_function_is_called_for_every_request():
    tokens = iter(["t1", "t2"])
    c, session = client(FakeResponse(payload={}), FakeResponse(payload={}), token=lambda: next(tokens))
    c.me()
    c.me()
    assert [call[2]["headers"]["Authorization"] for call in session.calls] == ["Bearer t1", "Bearer t2"]


def test_no_token_in_repr_or_errors():
    c, _ = client(FakeResponse(status=401, payload={"error": "sign in again"}))
    assert CANARY not in repr(c)
    with pytest.raises(api.StardeltApiError) as caught:
        c.me()
    assert CANARY not in str(caught.value) and CANARY not in repr(caught.value)


def test_default_token_comes_from_the_library(monkeypatch):
    import stardelt

    monkeypatch.setattr(stardelt, "_token", lambda: "from-library")
    c, session = client(FakeResponse(payload={}), token=None)
    c.me()
    assert session.calls[0][2]["headers"]["Authorization"] == "Bearer from-library"
