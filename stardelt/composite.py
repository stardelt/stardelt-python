"""Composite read tools for the MCP server: one call that replaces a short sequence of operations.

An assistant that has to list the namespaces, then the tables of each, then read a table's documentation, its metadata and
its history spends many turns and fills its context with envelopes. ``catalog_tree`` and ``describe_table`` do the
sequence in the server and return one small, bounded answer. They only read, through the same ``stardelt.api.Client``
and the same operations of the contract (``namespaces_list``, ``tables_list``, ``table_documentation_get``,
``table_metadata`` and ``table_history``); they are offered only when the operations they need are offered.
"""

from stardelt import api

MAX_NAMESPACES = 50
MAX_TABLES = 200
MAX_TOTAL_TABLES = 500
RECENT_SNAPSHOTS = 5


def _query(catalog):
    return {"catalog": catalog} if catalog else {}


def catalog_tree(nova, catalog=None, namespace=None):
    """The namespaces of a catalog, each with the names of its tables (sorted; at most MAX_TABLES per namespace and MAX_TOTAL_TABLES in all)."""
    listed = nova.call("namespaces_list", query=_query(catalog))["namespaces"]
    names = [".".join(levels) for levels in listed]
    if namespace is not None:
        names = [name for name in names if name == namespace]
        if not names:
            raise ValueError(f"no namespace {namespace}")
    tree, budget = [], MAX_TOTAL_TABLES
    for name in names[:MAX_NAMESPACES]:
        try:
            tables = sorted(i["name"] for i in nova.call("tables_list", path={"ns": name}, query=_query(catalog))["identifiers"])
        except api.StardeltApiError as err:
            tree.append({"namespace": name, "error": str(err)})
            continue
        kept = tables[: min(MAX_TABLES, budget)]
        budget -= len(kept)
        entry = {"namespace": name, "tables": kept}
        if len(tables) > len(kept):
            entry["tablesOmitted"] = len(tables) - len(kept)
        tree.append(entry)
    answer = {"namespaces": tree}
    if catalog:
        answer["catalog"] = catalog
    if len(names) > MAX_NAMESPACES:
        answer["namespacesOmitted"] = len(names) - MAX_NAMESPACES
    return answer


def _partitioning(metadata):
    """The fields of the table's default partition spec as ``{name, transform}``, or ``None`` when not readable."""
    for spec in metadata.get("partition-specs") or []:
        if isinstance(spec, dict) and spec.get("spec-id") == metadata.get("default-spec-id"):
            return [{"name": f.get("name"), "transform": f.get("transform")} for f in spec.get("fields") or [] if isinstance(f, dict)]
    return None


def describe_table(nova, namespace, table, catalog=None):
    """One table in one answer: its description, owner, tags and columns, where it lives, how it is partitioned, and its last snapshots.

    A part Nova refuses or cannot answer is left out with a sentence under ``<part>Error``; the rest is still returned.
    """
    path, query = {"ns": namespace, "tbl": table}, _query(catalog)
    docs = nova.call("table_documentation_get", path=path, query=query)
    answer = {"table": f"{namespace}.{table}"}
    for key in ("description", "owner", "domain", "tags", "columns"):
        answer[key] = docs.get(key)
    try:
        metadata = nova.call("table_metadata", path=path, query=query).get("metadata") or {}
        answer["location"] = metadata.get("location")
        answer["partitioning"] = _partitioning(metadata)
    except api.StardeltApiError as err:
        answer["metadataError"] = str(err)
    try:
        history = nova.call("table_history", path=path, query=query)
        names = [column["name"] for column in history["columns"]]
        answer["recentSnapshots"] = [dict(zip(names, row)) for row in history["rows"][:RECENT_SNAPSHOTS]]
    except api.StardeltApiError as err:
        answer["recentSnapshotsError"] = str(err)
    return answer
