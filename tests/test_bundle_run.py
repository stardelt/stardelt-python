"""``stardelt bundle run``: start what a deploy of one target left on the stage and wait for each run. No network."""

import json

from stardelt import runs
from tests.test_api import FakeResponse
from tests.test_bundle import make
from tests.test_bundle_summary import ME, run, stamped

LISTING = {"pipelines": [stamped("nightly"), stamped("load"), stamped("old-in-dev", target="dev"), stamped("theirs", bundle_name="other")]}
DEV = {
    "pipelines": [
        stamped("dev-alice-nightly", target="dev"),
        stamped("dev-alice-load", target="dev"),
        stamped("dev-bob-load", target="dev"),
    ]
}


def queued(run_id):
    return FakeResponse(payload={"runId": run_id, "state": "queued"})


def ended(run_id, state="success", tasks=()):
    return FakeResponse(payload={"run": {"runId": run_id, "state": state}, "tasks": [{"taskId": t, "state": s} for t, s in tasks]})


def lines(text):
    return [json.loads(line) for line in text.splitlines()]


def test_every_deployed_pipeline_of_the_target_runs_in_name_order_and_is_waited_for(tmp_path):
    make(tmp_path)
    code, out, err, session = run(
        ["bundle", "run", str(tmp_path), "-t", "prod", "--interval", "0"],
        ME, FakeResponse(payload=LISTING), queued("r1"), ended("r1"), queued("r2"), ended("r2"),
    )
    assert (code, err) == (0, "")
    assert lines(out) == [
        {"action": "ran", "name": "load", "runId": "r1", "state": "success"},
        {"action": "ran", "name": "nightly", "runId": "r2", "state": "success"},
    ]
    assert [method for method, _, _ in session.calls] == ["GET", "GET", "POST", "GET", "POST", "GET"]
    assert [url.removeprefix("https://nova.example") for _, url, _ in session.calls][2:] == [
        "/api/pipelines/stardelt/load/runs",
        "/api/pipelines/stardelt/load/runs/r1",
        "/api/pipelines/stardelt/nightly/runs",
        "/api/pipelines/stardelt/nightly/runs/r2",
    ]


def test_the_first_run_that_does_not_succeed_stops_the_rest_and_exits_1(tmp_path):
    make(tmp_path)
    code, out, err, session = run(
        ["bundle", "run", str(tmp_path), "-t", "prod", "--interval", "0"],
        ME, FakeResponse(payload=LISTING), queued("r1"), ended("r1", "failed", [("extract", "success"), ("load", "failed")]),
    )
    assert code == 1
    assert lines(out) == [{"action": "ran", "name": "load", "runId": "r1", "state": "failed", "failedTasks": ["load"]}]
    assert "stardelt: load ended failed" in err
    assert len(session.calls) == 4, "nightly was never started"


def test_only_names_one_pipeline_with_or_without_the_dev_prefix_and_never_runs_somebody_elses(tmp_path):
    make(tmp_path)
    for name in ("load", "dev-alice-load"):
        code, out, err, session = run(
            ["bundle", "run", str(tmp_path), "-t", "dev", "--only", name, "--interval", "0"],
            ME, FakeResponse(payload=DEV), queued("r1"), ended("r1"),
        )
        assert (code, err) == (0, "")
        assert lines(out) == [{"action": "ran", "name": "dev-alice-load", "runId": "r1", "state": "success"}]
        assert session.calls[2][1].endswith("/api/pipelines/stardelt/dev-alice-load/runs")


def test_an_unknown_name_and_an_empty_stage_are_problems_and_start_nothing(tmp_path):
    make(tmp_path)
    code, out, err, session = run(["bundle", "run", str(tmp_path), "-t", "prod", "--only", "nope"], ME, FakeResponse(payload=LISTING))
    assert (code, out) == (2, "")
    assert "nope: no pipeline of this bundle target is deployed under that name" in err
    assert len(session.calls) == 2

    code, out, err, session = run(["bundle", "run", str(tmp_path), "-t", "prod"], ME, FakeResponse(payload={"pipelines": []}))
    assert (code, out) == (2, "")
    assert "nothing is deployed from this bundle target: run `stardelt bundle deploy` first" in err
    assert len(session.calls) == 2


def test_a_bundle_problem_is_reported_before_any_request(tmp_path):
    make(tmp_path)
    code, out, err, session = run(["bundle", "run", str(tmp_path), "-t", "nope"])
    assert (code, out) == (2, "")
    assert "nope" in err
    assert session.calls == []


def test_giving_up_waiting_is_a_problem_not_a_failure_and_keeps_what_ended(tmp_path, monkeypatch):
    make(tmp_path)
    started = []

    def fake_start(nova, namespace, name, wait=False, timeout=None, interval=5):
        started.append((name, wait, timeout, interval))
        if name == "nightly":
            raise runs.RunError("run r2 is still running after 7 seconds; it was not stopped")
        return {"runId": "r1", "state": "success"}, []

    monkeypatch.setattr(runs, "start", fake_start)
    code, out, err, _ = run(
        ["bundle", "run", str(tmp_path), "-t", "prod", "--timeout", "7", "--interval", "2"], ME, FakeResponse(payload=LISTING)
    )
    assert code == 2
    assert lines(out) == [{"action": "ran", "name": "load", "runId": "r1", "state": "success"}]
    assert "stardelt: prod: nightly: run r2 is still running after 7 seconds; it was not stopped" in err
    assert started == [("load", True, 7, 2), ("nightly", True, 7, 2)]
