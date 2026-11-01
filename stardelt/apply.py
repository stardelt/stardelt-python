"""``stardelt apply -f pipeline.json``: create the pipeline, or update it when one of that name exists.

The file is a JSON or YAML object shaped like the body of ``POST /api/pipelines`` (operation ``pipelines_create``):
``name`` and ``ownerGroup``, one of ``sql`` or ``notebook``, and optionally ``schedule``, ``params`` and
``options``. Applying the same file twice changes nothing the second time. An update replaces what the file
says: ``params`` and ``options`` left out of the file go back to their defaults.
"""

import json
from pathlib import Path

import yaml

CREATE_ONLY = ("name", "ownerGroup")
SUFFIXES = (".json", ".yaml", ".yml")


class ApplyError(ValueError):
    """The file cannot be applied; the message is one sentence for the person running the command."""


def load(text):
    """The pipeline spec in ``text`` (JSON, else YAML), or an ``ApplyError``."""
    try:
        spec = json.loads(text)
    except ValueError:
        try:
            spec = yaml.safe_load(text)
        except yaml.YAMLError:
            raise ApplyError("the file is neither JSON nor YAML") from None
    if not isinstance(spec, dict) or not isinstance(spec.get("name"), str) or not spec["name"]:
        raise ApplyError('the file must be one JSON or YAML object with a "name"')
    try:
        json.dumps(spec)
    except TypeError:
        raise ApplyError("the file holds a value JSON cannot carry (an unquoted date?): put it in quotes") from None
    return spec


def load_path(path):
    """The specs at ``path``: one file, or every ``.json``, ``.yaml`` and ``.yml`` file directly inside a
    directory, in name order. Every file is read before anything is applied, so one bad file writes nothing."""
    where = Path(path)
    if not where.is_dir():
        return [load(where.read_text(encoding="utf-8"))]
    files = sorted(f for f in where.iterdir() if f.is_file() and f.suffix in SUFFIXES)
    if not files:
        raise ApplyError(f"{path} holds no .json, .yaml or .yml file")
    specs = []
    for f in files:
        try:
            specs.append(load(f.read_text(encoding="utf-8")))
        except ApplyError as err:
            raise ApplyError(f"{f.name}: {err}") from None
    names = [spec["name"] for spec in specs]
    for name in names:
        if names.count(name) > 1:
            raise ApplyError(f"two files define the pipeline {name}")
    return specs


def apply(nova, spec, dry_run=False):
    """Create or update the pipeline ``spec`` through ``nova`` (a ``stardelt.api.Client``).

    Returns ``{"action": ..., "name": ...}`` where the action is ``created`` or ``updated``. With
    ``dry_run`` nothing is written: Nova checks the document with every rule a create runs
    (``pipelines_validate``). A refusal is an ``ApplyError`` with Nova's sentence; an accepted document
    answers ``would-create`` or ``would-update``; an update also lists ``changes`` (which of ``statement`` or
    ``notebook``, ``schedule``, ``params`` and ``options`` it would change; empty when it changes nothing), and
    ``nextRuns`` (the next fire times) appears when the file has a schedule.
    """
    name = spec["name"]
    if dry_run:
        verdict = nova.call("pipelines_validate", body=spec)
        if not verdict["ok"]:
            raise ApplyError(f"{name}: {verdict['errors'][0]['message']}")
        result = {"action": f"would-{verdict['action']}", "name": name}
        if verdict["action"] == "update":
            result["changes"] = verdict["changes"]
        if verdict["nextRuns"]:
            result["nextRuns"] = verdict["nextRuns"]
        return result
    found = [p for p in nova.call("pipelines_list")["pipelines"] if p["name"] == name]
    if not found:
        nova.call("pipelines_create", body=spec)
        return {"action": "created", "name": name}
    existing = found[0]
    if "ownerGroup" in spec and spec["ownerGroup"] != existing.get("ownerGroup"):
        raise ApplyError(f"{name} belongs to the group {existing.get('ownerGroup')}: ownerGroup cannot change")
    body = {key: value for key, value in spec.items() if key not in CREATE_ONLY}
    nova.call("pipelines_update", path={"namespace": existing["namespace"], "name": name}, body=body)
    return {"action": "updated", "name": name}
