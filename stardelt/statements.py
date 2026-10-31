"""Run a long query through Nova's statement API (stardelt-nova#317), for ``stardelt sql --long`` and ``Client.statement``.

``POST /api/query`` holds one request open until the query ends and cuts the rows at 50 000. A *statement* is
submitted, polled and read in chunks, so no request stays open, up to 100 000 rows are kept, and the statement is
cancelled in the engine when the caller gives up (Ctrl-C) or when anything goes wrong.
"""

import time

from stardelt import api


class StatementError(RuntimeError):
    """The statement ended without rows; the message is one sentence for the person."""


def run(nova, sql, max_seconds=None, clock=time.monotonic):
    """Run one statement and return a ``QueryResult``. ``max_seconds`` (default: no limit) cancels it when it takes longer."""
    deadline = None if max_seconds is None else clock() + max_seconds
    view = nova.call("statements_submit", body={"sql": sql, "waitSeconds": 30})
    statement_id = view["statementId"]
    try:
        while view["state"] in ("PENDING", "RUNNING"):
            if deadline is not None and clock() >= deadline:
                raise StatementError(f"the statement did not finish in {max_seconds} seconds and was cancelled")
            view = nova.call("statements_get", path={"id": statement_id}, query={"waitSeconds": 10})
        if view["state"] != "SUCCEEDED":
            raise StatementError(view.get("error") or f"the statement ended {view['state']}")
        rows = list(view.get("rows") or [])
        for n in range(1, view.get("chunks", 0)):
            rows.extend(nova.call("statements_chunk_get", path={"id": statement_id, "n": n})["rows"])
        payload = {"columns": view["columns"], "rows": rows, "row_count": len(rows), "truncated": bool(view.get("truncated"))}
        return api.QueryResult(payload)
    finally:
        # Stops what still runs and frees one of the person's ten statement slots. A failing cancel must not hide
        # the rows or the error that is already on its way out; Nova forgets a statement after an hour anyway.
        try:
            nova.call("statements_cancel", path={"id": statement_id})
        except api.StardeltApiError:
            pass
