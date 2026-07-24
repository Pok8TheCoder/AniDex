"""Background job registry for long-running AniDex tasks."""

from __future__ import annotations

import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Job:
    id: str
    kind: str
    status: str = "pending"  # pending|running|done|error
    message: str = ""
    progress: int = 0
    total: int = 0
    result: Any = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "message": self.message,
            "progress": self.progress,
            "total": self.total,
            "result": self.result,
            "error": self.error,
        }


class JobRegistry:
    def __init__(self, max_workers: int = 4) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="LFJob")

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def submit(self, kind: str, fn: Callable[[Job], Any]) -> Job:
        job = Job(id=secrets.token_urlsafe(10), kind=kind)
        with self._lock:
            self._jobs[job.id] = job

        def run() -> None:
            job.status = "running"
            job.updated_at = time.time()
            try:
                result = fn(job)
                job.result = result
                job.status = "done"
                job.message = job.message or "Done"
            except Exception as exc:  # noqa: BLE001
                job.status = "error"
                job.error = str(exc)
                job.message = str(exc).splitlines()[0][:200]
            finally:
                job.updated_at = time.time()

        self._pool.submit(run)
        return job

    def shutdown(self) -> None:
        try:
            self._pool.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            self._pool.shutdown(wait=False)


JOBS = JobRegistry()
