"""Spark compute from code (internal#161 slice 6): list, start and stop a SparkCluster.

The operator owns the cluster; Nova exposes it. Nothing here starts a cluster implicitly —
the maintainer's decision: a person presses Start (or `stardelt.compute.start`).
"""

from __future__ import annotations

import time

from stardelt.api import Client


class ClusterStopped(RuntimeError):
    """The cluster exists and the caller may use it, but it is Stopped (or Failed)."""


def _client(client=None):
    return client or Client()


def list(client=None):
    """Every cluster this person may use, as Nova answers them (`GET /api/compute`)."""
    return _client(client).call("compute_list")["clusters"]


def get(name, client=None):
    """One cluster by name, or `LookupError` when it does not exist or the caller may not use it."""
    for cluster in list(client):
        if cluster["name"] == name:
            return cluster
    raise LookupError(f"no Spark cluster named {name!r} that you may use")


def start(name, wait=True, timeout=300, interval=2, client=None):
    """Start `name`; with `wait`, poll until the phase is Running.

    Answers the last view Nova returned. Raises `TimeoutError` when the cluster is still
    Starting after `timeout` seconds (the cluster is not stopped by this call), and
    `ClusterStopped` if it answers Failed.
    """
    client = _client(client)
    view = client.call("compute_start", path={"name": name})
    if not wait:
        return view
    deadline = time.monotonic() + timeout
    while view.get("phase") != "Running":
        if view.get("phase") == "Failed":
            raise ClusterStopped(f"{name} failed to start: {view.get('message') or 'no reason given'}")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"{name} is still {view.get('phase')} after {timeout}s")
        time.sleep(interval)
        view = get(name, client)
    return view


def stop(name, client=None):
    """Stop `name`; answers the updated view. Idempotent."""
    return _client(client).call("compute_stop", path={"name": name})
