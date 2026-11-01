"""``stardelt bundle summary``: what a deploy of one target left on the stage. No network: a fake session stands in."""

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


def stamped(name, bundle_name="sales-etl", target="prod", **more):
    return {
        "name": name,
        "namespace": "stardelt",
        "ownerGroup": "sales",
        "deployment": {"bundle": bundle_name, "target": target, **more},
    }


LISTING = {
    "pipelines": [
        stamped("nightly", revision="0123abc", deployedBy="ada", deployedAt="2026-11-01T08:00:00Z", modified=True),
        stamped("load", revision="0123abc-dirty", deployedBy="ada", deployedAt="2026-11-01T08:00:01Z"),
        stamped("bare"),
        stamped("old-in-dev", target="dev"),
        stamped("theirs", bundle_name="other-bundle"),
        {"name": "by-hand", "namespace": "stardelt", "ownerGroup": "sales"},
    ]
}


def test_one_row_per_pipeline_of_this_bundle_target_in_name_order_with_what_nova_knows(tmp_path):
    make(tmp_path)
    code, out, err, session = run(["bundle", "summary", str(tmp_path), "-t", "prod"], ME, FakeResponse(payload=LISTING))
    assert (code, err) == (0, "")
    assert [json.loads(line) for line in out.splitlines()] == [
        {"name": "bare"},
        {"name": "load", "revision": "0123abc-dirty", "deployedBy": "ada", "deployedAt": "2026-11-01T08:00:01Z"},
        {
            "name": "nightly",
            "revision": "0123abc",
            "deployedBy": "ada",
            "deployedAt": "2026-11-01T08:00:00Z",
            "modified": True,
        },
    ]
    assert [m for m, _ in [(m, u) for m, u, _ in session.calls]] == ["GET", "GET"], "a summary only reads"


def test_a_development_target_shows_only_the_callers_own_pipelines(tmp_path):
    make(tmp_path)
    listing = {
        "pipelines": [
            stamped("dev-alice-load", target="dev", revision="aaaaaaa"),
            stamped("dev-bob-load", target="dev", revision="bbbbbbb"),
        ]
    }
    code, out, _, _ = run(["bundle", "summary", str(tmp_path), "-t", "dev"], ME, FakeResponse(payload=listing))
    assert code == 0
    assert [json.loads(line)["name"] for line in out.splitlines()] == ["dev-alice-load"]


def test_nothing_deployed_says_so(tmp_path):
    make(tmp_path)
    code, out, err, _ = run(["bundle", "summary", str(tmp_path), "-t", "prod"], ME, FakeResponse(payload={"pipelines": []}))
    assert (code, out, err) == (0, "", "stardelt: nothing is deployed from this bundle target\n")


def test_a_bundle_problem_and_a_development_target_without_a_sign_in_name_are_refused_before_the_listing(tmp_path):
    make(tmp_path)
    code, out, err, session = run(["bundle", "summary", str(tmp_path), "-t", "nope"], ME)
    assert (code, out) == (2, "") and "no target named 'nope'" in err
    assert session.calls == [], "a bundle problem is found before anything is asked of Nova"
    code, out, err, session = run(
        ["bundle", "summary", str(tmp_path), "-t", "dev"], FakeResponse(payload={}), FakeResponse(payload=LISTING)
    )
    assert (code, out) == (2, "") and "sign in first" in err
    assert len(session.calls) == 1, "only the sign-in name was asked; no listing"
