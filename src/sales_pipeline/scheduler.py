"""Single-process interval scheduler. PostgreSQL serializes independent scheduler instances."""

import json
import signal
import threading

from sales_pipeline.runner import ConcurrentRun, run_path


def schedule(url, path, interval_seconds=300, iterations=None):
    if interval_seconds < 1 or (iterations is not None and iterations < 1):
        raise ValueError("positive_schedule_bounds_required")
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    count = 0
    while not stop.is_set():
        try:
            run_path(url, path)
        except ConcurrentRun:
            print(json.dumps(dict(event="schedule_skipped", reason="concurrent_run")), flush=True)
        except Exception as exc:
            print(
                json.dumps(dict(event="schedule_failed", error_code=type(exc).__name__)), flush=True
            )
        count += 1
        if iterations is not None and count >= iterations:
            break
        stop.wait(interval_seconds)
