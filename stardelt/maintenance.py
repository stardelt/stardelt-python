"""Run one catalog's Iceberg maintenance policy through Trino.

The operator's nightly CronJob runs ``stardelt maintenance run --catalog <c>`` as the team's
pipeline principal (operator#476). This module is that half: it lists the catalog's base
tables, optimizes the ones with too many small files, expires old snapshots and removes
orphan files — one JSON line per table, so the job's log says what happened to each table.
"""

import json
import re
import sys
import time

import stardelt

# The CRD's defaults (``spec.maintenance``); a key the caller leaves out takes these.
DEFAULT_FILE_SIZE_THRESHOLD = "128MB"
DEFAULT_MIN_SMALL_FILES = 20
DEFAULT_RETENTION = "7d"

# Powers of 1024, as the ticket says; the units Trino accepts in file_size_threshold.
_UNITS = {"b": 1, "kb": 1024, "mb": 1024**2, "gb": 1024**3}
_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(b|kb|mb|gb)\s*$", re.IGNORECASE)


def _bytes(text):
    """``"128MB"`` as bytes (134217728). Units B, kB, MB and GB, powers of 1024."""
    match = _SIZE_RE.match(str(text))
    if match is None:
        raise ValueError(f"{text!r} is not a size like '128MB' (units B, kB, MB, GB)")
    return int(float(match.group(1)) * _UNITS[match.group(2).lower()])


def _settings(policy):
    """The policy with every missing key filled from the CRD's defaults."""
    policy = policy or {}
    optimize = policy.get("optimize") or {}
    snapshots = policy.get("expireSnapshots") or {}
    orphans = policy.get("removeOrphanFiles") or {}
    min_small_files = optimize.get("minSmallFiles")
    return {
        "threshold": optimize.get("fileSizeThreshold") or DEFAULT_FILE_SIZE_THRESHOLD,
        "minSmallFiles": DEFAULT_MIN_SMALL_FILES if min_small_files is None else int(min_small_files),
        "snapshotRetention": snapshots.get("retention") or DEFAULT_RETENTION,
        "orphanRetention": orphans.get("retention") or DEFAULT_RETENTION,
        "exclude": list(policy.get("excludeTables") or []),
    }


def _tables(catalog):
    """The catalog's base tables as ``(schema, table)``, engine schemas left out."""
    return (
        f"SELECT table_schema, table_name FROM {stardelt._quote_ident(catalog)}.information_schema.tables "
        "WHERE table_type = 'BASE TABLE' AND table_schema <> 'information_schema'"
    )


def _qualified(*parts):
    return ".".join(stardelt._quote_ident(part) for part in parts)


def _maintain_table(cur, catalog, schema, table, settings, small_file_bytes):
    """Run the policy on one table through ``cur``; return ``(small_files, optimized)``."""
    qualified = _qualified(catalog, schema, table)
    files = _qualified(catalog, schema, table + "$files")
    threshold = settings["threshold"]
    snapshot_retention = settings["snapshotRetention"]
    orphan_retention = settings["orphanRetention"]
    cur.execute(
        f"SELECT count(*) FROM {files} WHERE file_size_in_bytes < {small_file_bytes}"
    )
    row = cur.fetchone()
    small_files = int(row[0]) if row else 0
    optimized = False
    if small_files >= settings["minSmallFiles"]:
        cur.execute(
            f"ALTER TABLE {qualified} EXECUTE optimize("
            f"file_size_threshold => '{threshold}')"
        )
        optimized = True
    cur.execute(
        f"ALTER TABLE {qualified} EXECUTE expire_snapshots("
        f"retention_threshold => '{snapshot_retention}')"
    )
    cur.execute(
        f"ALTER TABLE {qualified} EXECUTE remove_orphan_files("
        f"retention_threshold => '{orphan_retention}')"
    )
    return small_files, optimized


def run(catalog, policy, connect=None, out=None):
    """Run ``policy`` on every table of ``catalog``; return 0 when no table failed, else 1.

    One JSON line per table on ``out`` (``table``, ``smallFiles``, ``optimized``, ``seconds``,
    ``error``), then a summary line with the catalog and how many tables finished or failed.
    An exception in one table is recorded in that line and the loop goes on.
    """
    out = sys.stdout if out is None else out
    connect = stardelt._connect if connect is None else connect
    settings = _settings(policy)
    small_file_bytes = _bytes(settings["threshold"])
    excluded = set(settings["exclude"])

    done = failed = 0
    conn = connect(live=True)
    try:
        cur = conn.cursor()
        try:
            cur.execute(_tables(catalog))
            tables = [(row[0], row[1]) for row in cur.fetchall()]
        finally:
            cur.close()

        for schema, table in tables:
            name = f"{schema}.{table}"
            if name in excluded:
                continue
            started = time.monotonic()
            small_files = 0
            optimized = False
            error = None
            cur = conn.cursor()
            try:
                small_files, optimized = _maintain_table(
                    cur, catalog, schema, table, settings, small_file_bytes
                )
            except Exception as exc:  # noqa: BLE001 - one table's failure is one line, not the run's
                error = stardelt._one_line(exc)
            finally:
                cur.close()
            out.write(
                json.dumps(
                    {
                        "table": name,
                        "smallFiles": small_files,
                        "optimized": optimized,
                        "seconds": round(time.monotonic() - started, 3),
                        "error": error,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            if error is None:
                done += 1
            else:
                failed += 1
    finally:
        conn.close()

    out.write(
        json.dumps(
            {"catalog": catalog, "tablesDone": done, "tablesFailed": failed},
            ensure_ascii=False,
        )
        + "\n"
    )
    return 1 if failed else 0
