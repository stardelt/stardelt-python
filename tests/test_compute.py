"""Tests for stardelt.compute (the Spark clusters Nova exposes). No network: a fake session stands in."""

import pytest

import stardelt.api as api
import stardelt.compute as compute
from tests.test_api import FakeResponse, FakeSession


def client(*responses):
    session = FakeSession(*responses)
    return api.Client(base_url="https://nova.example", token="t", session=session), session


CLUSTERS = {
    "clusters": [
        {"name": "shared", "phase": "Running", "endpoint": "sc://shared:15002"},
        {"name": "trial", "phase": "Stopped", "endpoint": "sc://trial:15002"},
    ]
}


def test_list_calls_get_api_compute_and_answers_clusters():
    c, session = client(FakeResponse(payload=CLUSTERS))
    assert compute.list(c) == CLUSTERS["clusters"]
    assert session.calls[0][:2] == ("GET", "https://nova.example/api/compute")


def test_get_answers_one_cluster_and_refuses_an_unknown_name():
    c, _ = client(FakeResponse(payload=CLUSTERS))
    assert compute.get("trial", c)["phase"] == "Stopped"
    c, _ = client(FakeResponse(payload=CLUSTERS))
    with pytest.raises(LookupError, match="no Spark cluster named 'nope'"):
        compute.get("nope", c)


def test_start_without_wait_posts_start_and_answers_the_first_view():
    c, session = client(FakeResponse(payload={"name": "trial", "phase": "Starting"}))
    view = compute.start("trial", wait=False, client=c)
    assert view["phase"] == "Starting"
    assert session.calls[0][:2] == ("POST", "https://nova.example/api/compute/trial/start")


def test_start_with_wait_polls_get_until_running():
    c, session = client(
        FakeResponse(payload={"name": "trial", "phase": "Starting"}),
        FakeResponse(payload={"clusters": [{"name": "trial", "phase": "Starting"}]}),
        FakeResponse(payload={"clusters": [{"name": "trial", "phase": "Running"}]}),
    )
    view = compute.start("trial", interval=0, client=c)
    assert view["phase"] == "Running"
    assert [call[0] for call in session.calls] == ["POST", "GET", "GET"]
    assert session.calls[1][1] == "https://nova.example/api/compute"


def test_start_with_wait_times_out_while_still_starting():
    class RepeatingSession(FakeSession):
        """Answers in order, then repeats the last answer: the poll never sees another phase."""

        def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]

    session = RepeatingSession(
        FakeResponse(payload={"name": "trial", "phase": "Starting"}),
        FakeResponse(payload={"clusters": [{"name": "trial", "phase": "Starting"}]}),
    )
    c = api.Client(base_url="https://nova.example", token="t", session=session)
    with pytest.raises(TimeoutError, match="still Starting after 0.01s"):
        compute.start("trial", timeout=0.01, interval=0, client=c)
    assert session.calls[0][:2] == ("POST", "https://nova.example/api/compute/trial/start")


def test_stop_posts_stop():
    c, session = client(FakeResponse(payload={"name": "trial", "phase": "Stopping"}))
    assert compute.stop("trial", c)["phase"] == "Stopping"
    assert session.calls[0][:2] == ("POST", "https://nova.example/api/compute/trial/stop")
