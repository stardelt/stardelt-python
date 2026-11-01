"""``stardelt bundle destroy``: remove what a deploy of one target put on the stage. No network: a fake session stands in."""

import io
import json

from stardelt import cli
from tests.test_api import CANARY, FakeResponse, FakeSession
from tests.test_bundle import make

ENV = {"STARDELT_NOVA_URL": "https://nova.example", "STARDELT_TOKEN": CANARY}
ME = FakeResponse(payload={"user": "alice"})


def run(argv, *responses):
    out, err = io.StringIO(), io.StringIO()
    session = FakeSession(*responses)
    code = cli.main(argv, environ=ENV, stdout=out, stderr=err, session=session, stdin=io.StringIO(""))
    return code, out.getvalue(), err.getvalue(), session


def calls(session):
    return [(method, url.removeprefix("https://nova.example")) for method, url, _ in session.calls]


def stamped(name, bundle_name="sales-etl", target="prod"):
    return {"name": name, "namespace": "stardelt", "ownerGroup": "sales", "deployment": {"bundle": bundle_name, "target": target}}


LISTING = {
    "pipelines": [
        stamped("load"),
        stamped("nightly"),
        stamped("old-in-dev", target="dev"),
        stamped("theirs", bundle_name="other-bundle"),
        {"name": "by-hand", "namespace": "stardelt", "ownerGroup": "sales"},
    ]
}
WARNING = "stardelt: nothing was removed; add --yes to remove what is listed\n"


def test_without_yes_it_only_lists_what_this_bundle_target_deployed(tmp_path):
    make(tmp_path)
    code, out, err, session = run(["bundle", "destroy", str(tmp_path), "-t", "prod"], ME, FakeResponse(payload=LISTING))
    assert (code, err) == (0, WARNING)
    assert [json.loads(line) for line in out.splitlines()] == [
        {"action": "would-delete", "name": "load"},
        {"action": "would-delete", "name": "nightly"},
    ]
    assert calls(session) == [("GET", "/api/me"), ("GET", "/api/pipelines")], "nothing is deleted without --yes"


def test_with_yes_each_listed_pipeline_is_deleted_and_nothing_else(tmp_path):
    make(tmp_path)
    code, out, err, session = run(
        ["bundle", "destroy", str(tmp_path), "-t", "prod", "--yes"],
        ME, FakeResponse(payload=LISTING), FakeResponse(204), FakeResponse(204),
    )
    assert (code, err) == (0, "")
    assert [json.loads(line) for line in out.splitlines()] == [
        {"action": "deleted", "name": "load"},
        {"action": "deleted", "name": "nightly"},
    ]
    assert [(m, u) for m, u in calls(session) if m == "DELETE"] == [
        ("DELETE", "/api/pipelines/stardelt/load"),
        ("DELETE", "/api/pipelines/stardelt/nightly"),
    ]


def test_a_development_target_removes_only_the_callers_own_pipelines(tmp_path):
    make(tmp_path)
    listing = {
        "pipelines": [
            stamped("dev-alice-load", target="dev"),
            stamped("dev-bob-load", target="dev"),
            stamped("dev-alice-old", target="prod"),
        ]
    }
    code, out, _, _ = run(["bundle", "destroy", str(tmp_path), "-t", "dev"], ME, FakeResponse(payload=listing))
    assert code == 0
    assert [json.loads(line)["name"] for line in out.splitlines()] == ["dev-alice-load"]


def test_nothing_to_remove_says_so(tmp_path):
    make(tmp_path)
    code, out, err, _ = run(
        ["bundle", "destroy", str(tmp_path), "-t", "prod", "--yes"], ME, FakeResponse(payload={"pipelines": [stamped("x", target="dev")]})
    )
    assert (code, out) == (0, "")
    code, out, err, _ = run(["bundle", "destroy", str(tmp_path), "-t", "prod"], ME, FakeResponse(payload={"pipelines": []}))
    assert (code, out, err) == (0, "", "stardelt: nothing to remove\n")


def test_a_bundle_problem_and_a_development_target_without_a_sign_in_name_stop_before_anything_is_removed(tmp_path):
    make(tmp_path)
    code, out, err, session = run(["bundle", "destroy", str(tmp_path), "-t", "nope", "--yes"], ME)
    assert (code, out) == (2, "") and "no target named 'nope'" in err
    assert session.calls == [], "a bundle problem is found before anything is asked of Nova"
    code, out, err, session = run(
        ["bundle", "destroy", str(tmp_path), "-t", "dev", "--yes"], FakeResponse(payload={}), FakeResponse(payload=LISTING)
    )
    assert (code, out) == (2, "") and "sign in first" in err
    assert [m for m, _ in calls(session)] == ["GET"], "no listing, no delete"
