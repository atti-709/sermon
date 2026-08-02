"""Subprocess job manager: one pipeline job at a time, logs fanned out over SSE.

Python pipeline steps run as `python -m sermon.web.worker <kind>` and speak a
JSONL protocol on stdout (see worker.py); the render job is the Remotion CLI,
whose human output is scraped for progress. Everything else a subprocess
prints is forwarded verbatim as `log` events.
"""

import asyncio
import json
import os
import re
import sys
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")
LINE_SPLIT_RE = re.compile(r"\r\n|\r|\n")

RENDERED_RE = re.compile(r"Rendered (\d+)/(\d+)")
ENCODED_RE = re.compile(r"Encoded (\d+)/(\d+)")


class JobBusy(Exception):
    pass


@dataclass
class JobRecord:
    id: str
    kind: str
    argv: list[str]
    cwd: Path
    project_id: str | None = None
    clip_id: str | None = None
    state: str = "running"  # running | succeeded | failed | canceled
    proc: asyncio.subprocess.Process | None = None
    events: deque = field(default_factory=lambda: deque(maxlen=2000))  # (seq, name, payload)
    seq: int = 0
    progress: dict = field(default_factory=dict)
    result: dict | None = None
    error: str | None = None
    exit_code: int | None = None
    cleanup_files: list[Path] = field(default_factory=list)
    subscribers: set = field(default_factory=set)

    def snapshot(self) -> dict:
        log_tail = [p["line"] for s, n, p in list(self.events)[-100:] if n == "log"]
        return {
            "id": self.id,
            "kind": self.kind,
            "project_id": self.project_id,
            "clip_id": self.clip_id,
            "state": self.state,
            "progress": self.progress,
            "result": self.result,
            "error": self.error,
            "exit_code": self.exit_code,
            "log_tail": log_tail,
        }


class JobManager:
    def __init__(self) -> None:
        self.jobs: dict[str, JobRecord] = {}
        self.order: deque[str] = deque(maxlen=20)

    @property
    def current(self) -> JobRecord | None:
        for job_id in reversed(self.order):
            job = self.jobs[job_id]
            if job.state == "running":
                return job
        return None

    def get(self, job_id: str) -> JobRecord | None:
        return self.jobs.get(job_id)

    async def start(
        self,
        kind: str,
        argv: list[str],
        cwd: Path,
        project_id: str | None = None,
        clip_id: str | None = None,
    ) -> JobRecord:
        running = self.current
        if running is not None:
            raise JobBusy(f"a {running.kind} job is already running")
        job = JobRecord(
            id=uuid.uuid4().hex[:12], kind=kind, argv=argv, cwd=cwd,
            project_id=project_id, clip_id=clip_id,
        )
        self.jobs[job.id] = job
        if len(self.order) == self.order.maxlen:
            self.jobs.pop(self.order[0], None)
        self.order.append(job.id)
        asyncio.create_task(self._run(job))
        return job

    async def cancel(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if job is None or job.proc is None or job.state != "running":
            return
        job.state = "canceled"
        job.proc.terminate()
        try:
            await asyncio.wait_for(job.proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            job.proc.kill()

    def subscribe(self, job: JobRecord, last_seq: int = -1) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=4096)
        for seq, name, payload in list(job.events):
            if seq > last_seq:
                self._offer(queue, (seq, name, payload))
        if job.state != "running":
            self._offer(queue, (job.seq, "done", self._done_payload(job)))
        job.subscribers.add(queue)
        return queue

    def unsubscribe(self, job: JobRecord, queue: asyncio.Queue) -> None:
        job.subscribers.discard(queue)

    @staticmethod
    def _offer(queue: asyncio.Queue, item: tuple) -> None:
        try:
            queue.put_nowait(item)
        except asyncio.QueueFull:
            pass

    def _emit(self, job: JobRecord, name: str, payload: dict, record: bool = True) -> None:
        job.seq += 1
        if record:
            job.events.append((job.seq, name, payload))
        for queue in list(job.subscribers):
            self._offer(queue, (job.seq, name, payload))

    def _done_payload(self, job: JobRecord) -> dict:
        return {"state": job.state, "exit_code": job.exit_code, "result": job.result, "error": job.error}

    async def _run(self, job: JobRecord) -> None:
        try:
            job.proc = await asyncio.create_subprocess_exec(
                *job.argv,
                cwd=job.cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
        except OSError as exc:
            job.state = "failed"
            job.error = f"failed to start {job.argv[0]}: {exc}"
            self._emit(job, "done", self._done_payload(job))
            return
        await asyncio.gather(
            self._pump(job, job.proc.stdout, "out"),
            self._pump(job, job.proc.stderr, "err"),
        )
        job.exit_code = await job.proc.wait()
        if job.state != "canceled":
            job.state = "succeeded" if job.exit_code == 0 and job.error is None else "failed"
        for path in job.cleanup_files:
            path.unlink(missing_ok=True)
        self._emit(job, "done", self._done_payload(job))

    async def _pump(self, job: JobRecord, stream: asyncio.StreamReader, name: str) -> None:
        buffer = b""
        while True:
            chunk = await stream.read(8192)
            if not chunk:
                break
            buffer += chunk
            *lines, buffer_str = LINE_SPLIT_RE.split(buffer.decode("utf-8", errors="replace"))
            buffer = buffer_str.encode("utf-8")
            for line in lines:
                self._handle_line(job, line, name)
        if buffer:
            self._handle_line(job, buffer.decode("utf-8", errors="replace"), name)

    def _handle_line(self, job: JobRecord, line: str, stream: str) -> None:
        line = ANSI_RE.sub("", line).strip()
        if not line:
            return
        if stream == "out" and line.startswith("{"):
            if self._handle_protocol(job, line):
                return
        if job.kind == "render":
            progress = _parse_remotion_progress(line)
            if progress:
                job.progress = progress
                self._emit(job, "progress", progress, record=False)
        self._emit(job, "log", {"stream": stream, "line": line})

    def _handle_protocol(self, job: JobRecord, line: str) -> bool:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            return False
        if not isinstance(msg, dict) or "event" not in msg:
            return False
        event = msg.pop("event")
        if event == "progress":
            job.progress = msg
            self._emit(job, "progress", msg, record=False)
        elif event == "log":
            self._emit(job, "log", {"stream": "out", "line": str(msg.get("line", ""))})
        elif event == "result":
            job.result = msg
        elif event == "error":
            job.error = str(msg.get("message", "unknown error"))
            self._emit(job, "log", {"stream": "err", "line": f"error: {job.error}"})
        else:
            return False
        return True


def _parse_remotion_progress(line: str) -> dict | None:
    match = RENDERED_RE.search(line)
    if match:
        done, total = int(match.group(1)), int(match.group(2))
        return {"percent": round(done / total * 90, 1) if total else 0, "stage": "render", "detail": line}
    match = ENCODED_RE.search(line)
    if match:
        done, total = int(match.group(1)), int(match.group(2))
        return {"percent": round(90 + done / total * 10, 1) if total else 90, "stage": "encode", "detail": line}
    if "Bundling" in line or "Compositions" in line:
        return {"percent": None, "stage": "prepare", "detail": line}
    return None


def worker_argv(*args: str) -> list[str]:
    return [sys.executable, "-u", "-m", "sermon.web.worker", *args]


manager = JobManager()
