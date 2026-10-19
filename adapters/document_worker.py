"""Background processing for uploaded documents.

One worker thread takes job IDs from a queue and asks the ingestion
service to process them, one at a time, so uploads never slow down
questions and a burst of uploads uses bounded resources.

The queue lives in memory. After a restart the service's
resume_unfinished() puts unfinished jobs back (the catalog remembers them).
"""
import logging
import queue
import threading
from typing import Callable

logger = logging.getLogger(__name__)

_STOP = object()


class DocumentWorker:
    def __init__(self):
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._process: Callable[[str], None] | None = None

    def enqueue(self, job_id: str) -> None:
        self._queue.put(job_id)

    def start(self, process: Callable[[str], None]) -> None:
        if self._thread is not None:
            return
        self._process = process
        self._thread = threading.Thread(target=self._run, name="flowone-documents", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        if self._thread is None:
            return
        self._queue.put(_STOP)
        self._thread.join(timeout)
        self._thread = None

    def wait_until_idle(self) -> None:
        """For tests and scripts: block until every queued job is handled."""
        self._queue.join()

    def _run(self) -> None:
        while True:
            job_id = self._queue.get()
            try:
                if job_id is _STOP:
                    return
                self._process(job_id)
            except Exception:
                # The service records failures itself; this only keeps the
                # worker alive if something unexpected escapes.
                logger.exception("FLOWONE_DOCUMENT_WORKER unexpected error")
            finally:
                self._queue.task_done()
