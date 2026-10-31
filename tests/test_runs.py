"""Start a pipeline run and wait for it (stardelt.runs). No network: a fake session stands in for Nova."""

import pytest

import stardelt.api as api
from stardelt import runs
from tests.test_api import CANARY, FakeResponse, FakeSession


def nova(*responses):
    session = FakeSession(*responses)
    return api.Client(base_url="https://nova.example", token=CANARY, session=session), session


def run_view(state):
    return {"runId": "manual__1", "state": state, "type": "manual"}


def started(state="queued"):
    return FakeResponse(201, run_view(state))


def looked_at(state, *tasks):
    return FakeResponse(200, {"run": run_view(state), "tasks": [{"taskId": t, "state": s, "tryNumber": 1} for t, s in tasks]})


def sent(session):
    return [(m, u.rsplit("/api", 1)[1]) for m, u, _ in session.calls]


def test_without_wait_it_starts_one_run_and_looks_no_further():
    client, session = nova(started())
    run, tasks = runs.start(client, "etl", "daily")
    assert (run["runId"], run["state"], tasks) == ("manual__1", "queued", [])
    assert sent(session) == [("POST", "/pipelines/etl/daily/runs")]


def test_wait_looks_again_until_the_run_is_no_longer_queued_or_running():
    client, session = nova(started(), looked_at("running", ("load", "running")), looked_at("success", ("load", "success")))
    pauses = []
    run, tasks = runs.start(client, "etl", "daily", wait=True, interval=7, sleep=pauses.append)
    assert run["state"] == "success" and [t["taskId"] for t in tasks] == ["load"]
    assert pauses == [7, 7]
    assert sent(session) == [
        ("POST", "/pipelines/etl/daily/runs"),
        ("GET", "/pipelines/etl/daily/runs/manual__1"),
        ("GET", "/pipelines/etl/daily/runs/manual__1"),
    ]


def test_wait_returns_a_failed_run_with_its_tasks_instead_of_raising():
    client, _ = nova(started(), looked_at("failed", ("extract", "success"), ("load", "failed")))
    run, tasks = runs.start(client, "etl", "daily", wait=True, sleep=lambda _: None)
    assert run["state"] == "failed" and [(t["taskId"], t["state"]) for t in tasks] == [("extract", "success"), ("load", "failed")]


def test_wait_gives_up_after_the_timeout_and_does_not_stop_the_run():
    ticks = iter([0, 11])
    client, session = nova(started())
    with pytest.raises(runs.RunError, match="manual__1 is still queued after 10 seconds; it was not stopped"):
        runs.start(client, "etl", "daily", wait=True, timeout=10, clock=lambda: next(ticks), sleep=lambda _: None)
    assert sent(session) == [("POST", "/pipelines/etl/daily/runs")]
