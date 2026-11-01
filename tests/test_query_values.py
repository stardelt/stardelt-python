"""A boolean in ``query`` goes out as ``true`` or ``false`` (Python's ``True`` would be refused). No network."""

import stardelt.api as api


class FakeResponse:
    status_code = 200

    def json(self):
        return []


class FakeSession:
    def __init__(self):
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return FakeResponse()


def client():
    session = FakeSession()
    return api.Client(base_url="https://nova.example/", token="t", session=session), session


def test_booleans_in_a_query_become_true_and_false():
    c, session = client()
    c.call("catalog_search", query={"q": "taxi", "exact": True, "fuzzy": False})
    _, _, kwargs = session.calls[0]
    assert kwargs["params"] == {"q": "taxi", "exact": "true", "fuzzy": "false"}


def test_other_query_values_are_left_alone():
    c, session = client()
    c.call("catalog_search", query={"q": "taxi", "limit": 5, "zero": 0})
    _, _, kwargs = session.calls[0]
    assert kwargs["params"] == {"q": "taxi", "limit": 5, "zero": 0}


def test_no_query_sends_no_params():
    c, session = client()
    c.call("catalog_search")
    _, _, kwargs = session.calls[0]
    assert "params" not in kwargs
