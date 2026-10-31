"""``stardelt apply -f pipeline.json``: create the pipeline, or update it when one of that name exists.

The file is a JSON or YAML object shaped like the body of ``POST /api/pipelines`` (operation ``pipelines_create``):
``name`` and ``ownerGroup``, one of ``sql`` or ``notebook``, and optionally ``schedule``, ``params`` and
``options``. Applying the same file twice changes nothing the second time. An update replaces what the file
says: ``params`` and ``options`` left out of the file go back to their defaults.
"""

import json

import yaml

CREATE_ONLY = ("name", "ownerGroup")


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


def apply(nova, spec, dry_run=False):
    """Create or update the pipeline ``spec`` through ``nova`` (a ``stardelt.api.Client``).

    Returns ``{"action": ..., "name": ...}`` where the action is ``created``, ``updated`` or, with
    ``dry_run``, ``would-create`` or ``would-update`` (nothing is written then).
    """
    name = spec["name"]
    found = [p for p in nova.call("pipelines_list")["pipelines"] if p["name"] == name]
    if not found:
        if not dry_run:
            nova.call("pipelines_create", body=spec)
        return {"action": "would-create" if dry_run else "created", "name": name}
    existing = found[0]
    if "ownerGroup" in spec and spec["ownerGroup"] != existing.get("ownerGroup"):
        raise ApplyError(f"{name} belongs to the group {existing.get('ownerGroup')}: ownerGroup cannot change")
    if not dry_run:
        body = {key: value for key, value in spec.items() if key not in CREATE_ONLY}
        nova.call("pipelines_update", path={"namespace": existing["namespace"], "name": name}, body=body)
    return {"action": "would-update" if dry_run else "updated", "name": name}
