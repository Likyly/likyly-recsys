"""The free audit's jobs and the queue in front of its browser.

An audit runs in a thread of its own (AuditJob). Most audits read the HTML only and take a few
seconds. Those whose recommendation blocks are drawn by JavaScript also open up to three pages in
the audit's browser (renderer/), which shows one page at a time. BrowserQueue lends that browser
to one audit at a time, for all its pages in a row: each report is read the same way whatever else
runs, and the visitors behind see their place in the line.

In memory: the API runs as one process. A restart drops the jobs still running (the visitor's page
says so and offers to start again); finished reports are in the database anyway.
"""
import secrets
import threading
import time
from typing import Any, Callable, Optional

MAX_WAITING = 3  # audits waiting for the browser; one more gets a report read from the HTML
MAX_WAIT_SECONDS = 180  # never wait longer than this for the browser
ABANDONED_AFTER_SECONDS = 30  # a waiting audit whose page stopped asking leaves the line
TYPICAL_HOLD_SECONDS = 35.0  # an audit's three pages in the browser, before any is measured
JOB_TTL_SECONDS = 15 * 60  # a finished job answers its page this long
MAX_JOBS = 2000


class AuditJob:
    """One audit, as its page follows it: running, waiting for the browser (with its place in the
    line), then done (the report) or failed (the reason)."""

    def __init__(self, visitor: str = ""):
        self.id = secrets.token_urlsafe(16)
        self.visitor = visitor
        self.status = "running"  # running | waiting | done | failed
        self.ahead = 0  # audits before this one in front of the browser
        self.wait_seconds = 0
        self.report: Optional[dict[str, Any]] = None
        self.detail = ""
        self.blocked = False
        self.created = time.monotonic()
        self.last_seen = self.created
        self.finished_at: Optional[float] = None

    @property
    def finished(self) -> bool:
        return self.status in ("done", "failed")

    def seen(self) -> None:
        self.last_seen = time.monotonic()

    def done(self, report: dict[str, Any]) -> None:
        self.report, self.status, self.finished_at = report, "done", time.monotonic()

    def fail(self, detail: str, blocked: bool = False) -> None:
        self.detail, self.blocked, self.status, self.finished_at = detail, blocked, "failed", time.monotonic()

    def public(self) -> dict[str, Any]:
        body: dict[str, Any] = {"job": self.id, "status": self.status}
        if self.status == "waiting":
            body.update(ahead=self.ahead, wait_seconds=self.wait_seconds)
        elif self.status == "done":
            body["report"] = self.report
        elif self.status == "failed":
            body.update(detail=self.detail, blocked=self.blocked)
        return body


class BrowserQueue:
    """First come, first served: one audit holds the browser, up to MAX_WAITING wait their turn.
    `acquire` answers False (the audit then reads the HTML only) when the line is full, when the
    wait would pass MAX_WAIT_SECONDS, or when the visitor left (their page stopped asking)."""

    def __init__(self):
        self.cond = threading.Condition()
        self.holder: Optional[AuditJob] = None
        self.since = 0.0
        self.waiting: list[AuditJob] = []
        self.typical = TYPICAL_HOLD_SECONDS

    def _estimate(self, index: int) -> int:
        """Seconds before the audit at `index` in the line gets the browser."""
        left = max(3.0, self.typical - (time.monotonic() - self.since)) if self.holder else 0.0
        return int(round(left + index * self.typical))

    def _place(self) -> None:
        for index, job in enumerate(self.waiting):
            job.ahead = index + (1 if self.holder else 0)
            job.wait_seconds = self._estimate(index)

    def acquire(self, job: AuditJob, max_wait: float = MAX_WAIT_SECONDS) -> bool:
        with self.cond:
            if self.holder is None and not self.waiting:
                self.holder, self.since = job, time.monotonic()
                return True
            if len(self.waiting) >= MAX_WAITING:
                return False
            self.waiting.append(job)
            job.status = "waiting"
            self._place()
            start = time.monotonic()
            try:
                while self.holder is not None or self.waiting[0] is not job:
                    now = time.monotonic()
                    if now - start > max_wait or now - job.last_seen > ABANDONED_AFTER_SECONDS:
                        return False
                    self.cond.wait(1.0)
                    self._place()
                self.holder, self.since = job, time.monotonic()
                return True
            finally:
                if job in self.waiting:
                    self.waiting.remove(job)
                    self._place()
                    self.cond.notify_all()
                job.status, job.ahead, job.wait_seconds = "running", 0, 0

    def release(self, job: AuditJob) -> None:
        with self.cond:
            if self.holder is not job:
                return
            held = time.monotonic() - self.since
            self.typical = 0.7 * self.typical + 0.3 * min(max(held, 5.0), 90.0)
            self.holder = None
            self._place()
            self.cond.notify_all()

    def reset(self) -> None:
        with self.cond:
            self.holder, self.waiting, self.typical = None, [], TYPICAL_HOLD_SECONDS
            self.cond.notify_all()


class JobBoard:
    """The jobs the visitors' pages follow, kept JOB_TTL_SECONDS after they finish."""

    def __init__(self):
        self.lock = threading.Lock()
        self.jobs: dict[str, AuditJob] = {}

    def start(self, job: AuditJob, work: Callable[[], None]) -> None:
        with self.lock:
            now = time.monotonic()
            self.jobs = {k: j for k, j in self.jobs.items() if not j.finished_at or now - j.finished_at < JOB_TTL_SECONDS}
            if len(self.jobs) >= MAX_JOBS:  # never grows without bound: the oldest finished go first
                for key in sorted((k for k, j in self.jobs.items() if j.finished), key=lambda k: self.jobs[k].created)[: len(self.jobs) // 4]:
                    del self.jobs[key]
            self.jobs[job.id] = job
        threading.Thread(target=work, name=f"audit-{job.id[:6]}", daemon=True).start()

    def get(self, job_id: str) -> Optional[AuditJob]:
        with self.lock:
            return self.jobs.get(job_id)

    def running_for(self, visitor: str) -> int:
        with self.lock:
            return sum(1 for j in self.jobs.values() if j.visitor == visitor and not j.finished)

    def reset(self) -> None:
        with self.lock:
            self.jobs.clear()
