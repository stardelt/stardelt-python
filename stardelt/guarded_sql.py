"""Guarded SQL for agents: ``sql_read`` and ``sql_explain`` of the MCP server (stardelt-nova#113).

An agent's main job is to write and check SQL, so the safe path is the default: one statement, read-only,
bounded, time-limited, cancelled when it takes too long. The classifier here is only a *first filter*: the
engine's own permissions stay the control (the statement runs as the signed-in person), and a writing
statement is never sent. The rows come through the statement API (``statements_submit`` and friends), so a
slow query is cancelled in the engine instead of left running.
"""

import json
import time

DEFAULT_ROWS = 200
MAX_ROWS = 1000
TIMEOUT_SECONDS = 60
READ_KEYWORDS = ("select", "with", "values", "show", "describe", "table")


class Refused(ValueError):
    """The statement is not one this tool runs; the message is one sentence for the agent."""


def _strip(sql):
    """``sql`` without comments (quoted text kept), and whether a second statement follows the first."""
    out, semicolons, quote, i, n = [], [], None, 0, len(sql)
    while i < n:
        c = sql[i]
        if quote:
            out.append(c)
            if c == quote:
                quote = None
            i += 1
        elif c in "'\"":
            quote = c
            out.append(c)
            i += 1
        elif sql.startswith("--", i):
            while i < n and sql[i] != "\n":
                i += 1
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            i = n if end < 0 else end + 2
            out.append(" ")
        else:
            if c == ";":
                semicolons.append(len(out))
            out.append(c)
            i += 1
    text = "".join(out)
    return text, any(text[at + 1 :].strip(" \t\r\n;") for at in semicolons)


def classify(sql, explain=False):
    """The statement to send, or ``Refused``. ``explain`` puts ``EXPLAIN`` in front of a read statement."""
    if not isinstance(sql, str) or not sql.strip():
        raise Refused("the statement is empty")
    text, another = _strip(sql)
    if another:
        raise Refused("exactly one statement is allowed")
    statement = text.strip().rstrip(";").strip()
    words = statement.lower().split()
    if not words:
        raise Refused("the statement is empty")
    first = words[0].lstrip("(")
    if first == "explain":
        # EXPLAIN plans without running; EXPLAIN ANALYZE runs the statement and is refused.
        if len(words) > 1 and words[1] == "analyze":
            raise Refused("EXPLAIN ANALYZE runs the statement and is refused; use EXPLAIN")
        return statement
    if first not in READ_KEYWORDS:
        raise Refused(f"only reading statements run here (SELECT, WITH, VALUES, SHOW, DESCRIBE, TABLE), not {first.upper()}")
    return "EXPLAIN " + statement if explain else statement


def run(nova, sql, max_rows=None, explain=False, clock=time.monotonic):
    """Run one guarded read statement and return the JSON text of its rows (at most ``max_rows``)."""
    statement = classify(sql, explain=explain)
    limit = DEFAULT_ROWS if max_rows is None else max_rows
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_ROWS:
        raise Refused(f"maxRows must be a whole number from 1 to {MAX_ROWS}")
    deadline = clock() + TIMEOUT_SECONDS
    view = nova.call("statements_submit", body={"sql": statement, "waitSeconds": 30})
    statement_id = view["statementId"]
    try:
        while view["state"] in ("PENDING", "RUNNING"):
            if clock() >= deadline:
                raise Refused(
                    f"the statement did not finish in {TIMEOUT_SECONDS} seconds and was cancelled; aggregate or add a LIMIT"
                )
            view = nova.call("statements_get", path={"id": statement_id}, query={"waitSeconds": 10})
        if view["state"] != "SUCCEEDED":
            raise Refused(view.get("error") or f"the statement ended {view['state']}")
        rows = (view.get("rows") or [])[:limit]
        cut = view.get("rowCount", len(rows)) > len(rows) or bool(view.get("truncated"))
        answer = {"columns": view.get("columns", []), "rows": rows, "rowCount": len(rows), "truncated": cut}
        if cut:
            answer["hint"] = "more rows exist: aggregate or add a LIMIT"
        return json.dumps(answer, ensure_ascii=False)
    finally:
        # Cancels what still runs and frees one of the person's ten statement slots.
        nova.call("statements_cancel", path={"id": statement_id})
