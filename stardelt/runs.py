"""Start a pipeline run and wait for it (``stardelt run NAMESPACE NAME --wait``), for CI jobs and scripts.

Nova's ``pipelines_run_trigger`` queues a run and answers at once; ``pipelines_run_get`` shows the run and its tasks.
This module joins the two. It never stops a run it gave up waiting for: stopping marks the run failed but cannot
cancel work already in flight (stardelt-nova#255), so that stays a deliberate act (``stardelt pipelines run-stop``).
"""

import time

ACTIVE = ("queued", "running")


class RunError(RuntimeError):
    """The run did not end in the time given; the message is one sentence for the person."""


def start(nova, namespace, name, wait=False, timeout=None, interval=5, clock=time.monotonic, sleep=time.sleep):
    """Start a run now and return ``(run, tasks)``.

    Without ``wait`` that is the queued run and ``[]``. With ``wait`` it returns once the run is no longer
    ``queued`` or ``running`` (any other state is the end), with the run's tasks; ``timeout`` (seconds, default
    no limit) raises ``RunError`` instead, and the run keeps going.
    """
    path = {"namespace": namespace, "name": name}
    run = nova.call("pipelines_run_trigger", path=path)
    if not wait:
        return run, []
    deadline = None if timeout is None else clock() + timeout
    tasks = []
    while run["state"] in ACTIVE:
        if deadline is not None and clock() >= deadline:
            raise RunError(f"run {run['runId']} is still {run['state']} after {timeout} seconds; it was not stopped")
        sleep(interval)
        answer = nova.call("pipelines_run_get", path=dict(path, run_id=run["runId"]))
        run, tasks = answer["run"], answer["tasks"]
    return run, tasks
