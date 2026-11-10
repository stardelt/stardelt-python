"""Where a table comes from and what depends on it (``stardelt lineage``), from Nova's lineage graph.

The answer is the Lineage tab's (``GET /api/lineage``, ``lineage_graph``): nodes with a level
(0 is the table asked about, negative upstream, positive downstream), the edges jobs drew, and
the read-only queries as ``readers`` on a node. This module reduces it to rows; it decides nothing.
"""


class TableRefError(ValueError):
    """The table was not named as ``catalog.schema.table``."""


def table_ref(text):
    """``"mobility.gold.kpis"`` -> ``("mobility", "gold", "kpis")``.

    Split at the last dot for the table and the first dot for the catalog, so a nested
    Iceberg namespace (``warehouse.team.gold``) stays one schema, exactly like the UI's
    ``lineageHref`` splits ``node.namespace``.
    """
    head, _, table = text.rpartition(".")
    catalog, _, schema = head.partition(".")
    if not head or not table or not catalog or not schema:
        raise TableRefError(f"name the table as catalog.schema.table, not {text!r}")
    return catalog, schema, table


def graph(nova, table, depth=2, days=30):
    """The lineage graph of one table, as Nova answers it."""
    catalog, schema, name = table_ref(table)
    return nova.call(
        "lineage_graph",
        query={"catalog": catalog, "schema": schema, "table": name, "depth": depth, "days": days},
    )


def _job_of(node_id, edges):
    """The job that most recently wrote this node: the newest edge ending in it."""
    writes = [edge for edge in edges if edge.get("to") == node_id]
    if not writes:
        return ""
    newest = max(writes, key=lambda edge: edge.get("eventTime") or "")
    namespace, name = newest.get("jobNamespace") or "", newest.get("jobName") or ""
    return f"{namespace}/{name}" if namespace else name


def _readers(node):
    readers = node.get("readers")
    if not readers:
        return "no reads"
    return f"{readers.get('people', 0)} people, {readers.get('queries', 0)} queries"


def rows(answer, direction=None):
    """One row per visible node: ``level``, ``dataset``, ``job``, ``lastWrite``, ``readers``.

    ``direction`` is ``"up"`` (level < 0), ``"down"`` (level > 0) or ``None`` (both and the
    table itself). Sorted by level, then by name, which puts the table in the middle.
    """
    edges = answer.get("edges") or []
    found = []
    for node in answer.get("nodes") or []:
        if not node.get("visible", True):
            continue
        level = node.get("level")
        if level is None:
            continue
        if direction == "up" and level >= 0:
            continue
        if direction == "down" and level <= 0:
            continue
        found.append(
            {
                "level": level,
                "dataset": f"{node.get('namespace', '')}.{node.get('name', '')}",
                "job": _job_of(node.get("id"), edges),
                "lastWrite": node.get("lastWrite") or "",
                "readers": _readers(node),
            }
        )
    return sorted(found, key=lambda row: (row["level"], row["dataset"]))


def jobs(answer):
    """The jobs of the answer, one line each: name, runs and last run."""
    return [
        f"{job.get('kind', '')} {job.get('name', '')}  {job.get('runs', 0)} runs, last {job.get('lastRun', '')}"
        for job in answer.get("jobs") or []
    ]
