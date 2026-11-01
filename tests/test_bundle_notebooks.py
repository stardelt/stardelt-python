"""A notebook that is a file of the bundle: ``plan`` asks Nova what a publish would do, ``deploy`` publishes and pins. No network."""

import json

from stardelt import bundle
from tests.test_api import FakeResponse
from tests.test_bundle import MANIFEST, make, said
from tests.test_bundle_diff import CREATE, ME, run, update

NOTEBOOK = {"nbformat": 4, "cells": []}
EXPLORE = "name: explore\nnotebook: {file: notebooks/explore.ipynb, params: {rows: '5'}}\n"
RESOURCES = {"explore.yaml": EXPLORE}
FILES = {"notebooks/explore.ipynb": json.dumps(NOTEBOOK), "sql/unused.sql": "select 1"}
SAME = FakeResponse(payload={"name": "explore.ipynb", "group": "sales", "objectVersion": "v7", "unchanged": True})
NEW = FakeResponse(payload={"name": "explore.ipynb", "group": "sales", "objectVersion": "", "unchanged": False})
STORED = FakeResponse(payload={"name": "explore.ipynb", "group": "sales", "objectVersion": "v8", "unchanged": False})
PINNED = {"name": "explore.ipynb", "objectVersion": "v8", "params": {"rows": "5"}}


def urls(session):
    return [(method, url.removeprefix("https://nova.example")) for method, url, _ in session.calls]


def test_plan_of_an_unchanged_notebook_checks_the_pipeline_against_the_version_it_has(tmp_path):
    make(tmp_path, resources=RESOURCES, files=FILES)
    code, out, _, session = run(["bundle", "plan", str(tmp_path), "-t", "prod"], ME, SAME, FakeResponse(payload=update("notebook")))
    assert code == 0
    assert [json.loads(line) for line in out.splitlines()] == [{"action": "would-update", "name": "explore", "changes": ["notebook"]}]
    assert urls(session)[1] == ("PUT", "/api/notebooks/sales/explore.ipynb")
    _, _, check = session.calls[1]
    assert check["params"] == {"dryRun": "true"} and check["json"] == NOTEBOOK
    _, _, validate = session.calls[2]
    assert validate["json"]["notebook"] == {"name": "explore.ipynb", "objectVersion": "v7", "params": {"rows": "5"}}


def test_plan_of_a_changed_notebook_says_would_publish_and_asks_nothing_more(tmp_path):
    make(tmp_path, resources=RESOURCES, files=FILES)
    code, out, _, session = run(["bundle", "plan", str(tmp_path), "-t", "prod"], ME, NEW)
    assert code == 0
    assert [json.loads(line) for line in out.splitlines()] == [{"action": "would-publish", "name": "explore", "notebook": "sales/explore.ipynb"}]
    assert len(session.calls) == 2, "no pipeline check: there is no version to pin yet"


def test_deploy_publishes_the_notebook_first_and_pins_the_version(tmp_path):
    make(tmp_path, resources=RESOURCES, files=FILES)
    code, out, _, session = run(
        ["bundle", "deploy", str(tmp_path), "-t", "prod"],
        ME, NEW, STORED, FakeResponse(payload=CREATE), FakeResponse(payload={"pipelines": []}), FakeResponse(payload={}),
    )
    assert code == 0
    assert [json.loads(line) for line in out.splitlines()] == [
        {"action": "published", "name": "explore", "notebook": "sales/explore.ipynb", "objectVersion": "v8"},
        {"action": "created", "name": "explore"},
    ]
    assert [method for method, _ in urls(session)] == ["GET", "PUT", "PUT", "POST", "GET", "POST"]
    assert "params" not in session.calls[2][2], "the second publish is a real one"
    assert session.calls[5][2]["json"]["notebook"] == PINNED


def test_deploy_of_an_unchanged_notebook_publishes_nothing(tmp_path):
    make(tmp_path, resources=RESOURCES, files=FILES)
    listing = FakeResponse(payload={"pipelines": [{"name": "explore", "namespace": "stardelt", "ownerGroup": "sales"}]})
    code, out, _, session = run(
        ["bundle", "deploy", str(tmp_path), "-t", "prod"], ME, SAME, FakeResponse(payload=update()), listing, FakeResponse(payload={})
    )
    assert code == 0
    assert [json.loads(line) for line in out.splitlines()] == [{"action": "updated", "name": "explore"}]
    assert sum(1 for method, url in urls(session) if (method, url) == ("PUT", "/api/notebooks/sales/explore.ipynb")) == 1


def test_a_notebook_nova_refuses_stops_the_plan_with_novas_sentence(tmp_path):
    make(tmp_path, resources=RESOURCES, files=FILES)
    refusal = FakeResponse(status=403, payload={"error": "you are not a member of the group sales"})
    code, out, err, session = run(["bundle", "plan", str(tmp_path), "-t", "prod"], ME, refusal)
    assert code == 2 and out == ""
    assert "explore: notebooks/explore.ipynb: you are not a member of the group sales (HTTP 403)" in err
    assert len(session.calls) == 2


def test_a_file_that_is_not_json_is_refused_before_any_request(tmp_path):
    make(tmp_path, resources=RESOURCES, files={"notebooks/explore.ipynb": "{not json", "sql/unused.sql": "select 1"})
    code, _, err, session = run(["bundle", "plan", str(tmp_path), "-t", "prod"], ME)
    assert code == 2
    assert "explore: notebooks/explore.ipynb is not a notebook" in err
    assert len(session.calls) == 1, "only the sign-in lookup"


def test_a_refusal_after_the_publish_still_lists_what_was_published(tmp_path):
    make(tmp_path, resources=RESOURCES, files=FILES)
    refused = FakeResponse(payload={"ok": False, "action": "create", "errors": [{"message": "no such schedule"}], "changes": [], "nextRuns": []})
    code, out, err, _ = run(["bundle", "deploy", str(tmp_path), "-t", "prod"], ME, NEW, STORED, refused)
    assert code == 2
    assert [json.loads(line) for line in out.splitlines()] == [
        {"action": "published", "name": "explore", "notebook": "sales/explore.ipynb", "objectVersion": "v8"}
    ]
    assert "explore: no such schedule" in err


def test_validate_checks_the_file_name_and_refuses_a_file_beside_a_version(tmp_path):
    resources = {
        "a.yaml": "name: a\nnotebook: {file: notebooks/bad$name.ipynb}\n",
        "b.yaml": "name: b\nnotebook: {file: notebooks/explore.ipynb, objectVersion: v1}\n",
    }
    make(tmp_path, resources=resources, files={"notebooks/bad$name.ipynb": "{}", "notebooks/explore.ipynb": "{}", "sql/unused.sql": "select 1"})
    problems, _, _ = bundle.validate(tmp_path, "prod")
    text = said(problems)
    assert "a.yaml (prod): notebook file 'notebooks/bad$name.ipynb': the file name must end in .ipynb" in text
    assert "b.yaml (prod): a notebook that names a file cannot also name a version: remove name and objectVersion" in text


def test_a_pinned_notebook_without_parameters_has_none():
    spec = {"name": "x", "notebook": {"file": "notebooks/a b.ipynb"}}
    assert bundle._pinned(spec, {"objectVersion": "v1"})["notebook"] == {"name": "a b.ipynb", "objectVersion": "v1"}


def test_a_drift_block_found_after_the_publish_refuses_the_deploy_and_keeps_the_published_line(tmp_path):
    manifest = MANIFEST.replace("prod: {mode: production,", "prod: {mode: production, drift: block,")
    make(tmp_path, manifest=manifest, resources=RESOURCES, files=FILES)
    by_hand = FakeResponse(payload={**update("notebook"), "modified": True})
    code, out, err, session = run(["bundle", "deploy", str(tmp_path), "-t", "prod"], ME, NEW, STORED, by_hand)
    assert code == 2
    assert [json.loads(line)["action"] for line in out.splitlines()] == ["published"]
    assert "explore was changed after its last deploy and this target says drift: block" in err
    assert len(session.calls) == 4, "nothing was applied"
