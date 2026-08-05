"""Subprocess job manager: one pipeline job at a time, logs fanned out over SSE.

Python pipeline steps run as `python -m sermon.web.worker <kind>` and speak a
JSONL protocol on stdout (see worker.py); the render job is the Remotion CLI,
whose human output is scraped for progress. Everything else a subprocess
prints is forwarded verbatim as `log` events.

Every job runs in its own process group (`start_new_session`), because the work
is never done by the process we start: the vertical export spawns ffmpeg, the
render spawns node, which spawns a headless Chrome and an ffmpeg of its own.
Cancelling has to reach all of them — signalling only the direct child leaves the
grandchildren running to completion, so a cancelled export still wrote its clip
and a cancelled render still wrote its mp4. The group covers most of it; Chrome
puts itself in a fresh group of its own, so the process tree is collected too,
while the parent links still exist.
"""

import asyncio
import contextlib
import json
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")
LINE_SPLIT_RE = re.compile(r"\r\n|\r|\n")

RENDERED_RE = re.compile(r"Rendered (\d+)/(\d+)")
ENCODED_RE = re.compile(r"Encoded (\d+)/(\d+)")

TERM_GRACE_SEC = 3.0  # ffmpeg exits on SIGTERM in well under a second; Remotion ignores it
KILL_GRACE_SEC = 3.0  # after SIGKILL, how long to wait for the last process to go


class JobBusy(Exception):
    pass


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # someone else's process now — not ours to wait on either way
    return True


def _process_tree(root: int, pgid: int | None) -> set[int]:
    """Every pid belonging to a job: `root`, everything below it, and its process group.

    Both halves are needed. The group catches processes that re-parent to PID 1 the
    moment their parent dies; the tree catches Chrome, which launches itself into a
    brand-new group that `killpg` would never touch. Read from one `ps` snapshot, so
    it has to be taken before anything is signalled — afterwards the parent links
    that lead to Chrome are gone."""
    listing = subprocess.run(
        ["ps", "-Ao", "pid=,ppid=,pgid="], capture_output=True, text=True, check=False
    ).stdout
    children: dict[int, list[int]] = {}
    found = {root}
    for line in listing.splitlines():
        try:
            pid, parent, group = (int(value) for value in line.split()[:3])
        except ValueError:
            continue
        children.setdefault(parent, []).append(pid)
        if pgid is not None and group == pgid:
            found.add(pid)

    queue = list(found)
    while queue:
        for child in children.get(queue.pop(), ()):
            if child not in found:
                found.add(child)
                queue.append(child)
    # never signal the server itself, its group, or init
    found -= {1, os.getpid()}
    with contextlib.suppress(OSError):
        own_group = os.getpgid(0)
        if pgid == own_group:  # start_new_session failed — don't take the server down
            return set()
    return found


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
    pgid: int | None = None  # captured at spawn: the leader may exit before its children
    canceling: bool = False
    started_at: float = field(default_factory=time.time)
    events: deque = field(default_factory=lambda: deque(maxlen=2000))  # (seq, name, payload)
    seq: int = 0
    progress: dict = field(default_factory=dict)
    result: dict | None = None
    error: str | None = None
    exit_code: int | None = None
    cleanup_files: list[Path] = field(default_factory=list)
    outputs: list[Path] = field(default_factory=list)  # discarded when the job is canceled
    subscribers: set = field(default_factory=set)

    def snapshot(self) -> dict:
        log_tail = [p["line"] for s, n, p in list(self.events)[-100:] if n == "log"]
        return {
            "id": self.id,
            "kind": self.kind,
            "project_id": self.project_id,
            "clip_id": self.clip_id,
            "state": self.state,
            "canceling": self.canceling,
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
        """Stop a job and everything it spawned.

        The job stays `running` until its whole process group is gone, so the busy
        lock cannot be handed to a restart while a doomed ffmpeg is still writing
        the same output path. `_run` finishes the job off: it marks it canceled and
        deletes the half-written file."""
        job = self.jobs.get(job_id)
        if job is None or job.state != "running" or job.canceling:
            return
        job.canceling = True
        self._emit(job, "log", {"stream": "err", "line": "canceling — stopping every process this job started…"})
        if job.proc is None:
            return  # canceled before it was spawned; _run stops it the moment it is
        await self._stop(job)

    async def _stop(self, job: JobRecord) -> None:
        """SIGTERM the job's processes, then SIGKILL whatever is left."""
        victims = await asyncio.to_thread(_process_tree, job.proc.pid, job.pgid)
        self._signal(job, victims, signal.SIGTERM)
        if not await self._await_exit(job, victims, TERM_GRACE_SEC):
            self._emit(job, "log", {"stream": "err",
                                    "line": f"{len(victims)} processes ignored SIGTERM — killing them"})
            victims |= await asyncio.to_thread(_process_tree, job.proc.pid, job.pgid)
            self._signal(job, victims, signal.SIGKILL)
            await self._await_exit(job, victims, KILL_GRACE_SEC)

    def _signal(self, job: JobRecord, pids: set[int], sig: int) -> None:
        """Signal the job's process group and every pid collected from its tree.

        Both, because neither is complete on its own: the group misses Chrome, and
        the snapshot misses whatever was spawned since it was taken."""
        own_group = os.getpgid(0)
        if job.pgid is not None and job.pgid != own_group:  # never signal the server's own group
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(job.pgid, sig)
        for pid in sorted(pids, reverse=True):  # youngest first: children before parents
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(pid, sig)

    async def _await_exit(self, job: JobRecord, pids: set[int], timeout: float) -> bool:
        """True once every process of the job is gone."""
        leader = job.proc.pid if job.proc is not None else None
        others = pids - {leader}
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            leader_gone = job.proc is None or job.proc.returncode is not None
            if leader_gone and not any(_pid_alive(pid) for pid in others):
                return True
            await asyncio.sleep(0.1)
        return False

    async def shutdown(self) -> None:
        """Take running jobs down with the server.

        Each job has its own session, so Ctrl-C on the terminal no longer reaches
        the children by itself — without this, quitting `sermon web` would leave an
        encode or a render orphaned."""
        for job_id in list(self.order):
            job = self.jobs.get(job_id)
            if job is not None and job.state == "running":
                await self.cancel(job_id)

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
                start_new_session=True,  # its own process group, so cancel can reach ffmpeg/node/Chrome
            )
            job.pgid = os.getpgid(job.proc.pid)
        except OSError as exc:
            job.state = "failed"
            job.error = f"failed to start {job.argv[0]}: {exc}"
            self._emit(job, "done", self._done_payload(job))
            return
        if job.canceling:  # cancel landed while the process was still starting up
            await self._stop(job)
        await asyncio.gather(
            self._pump(job, job.proc.stdout, "out"),
            self._pump(job, job.proc.stderr, "err"),
        )
        job.exit_code = await job.proc.wait()
        if job.canceling:
            job.state = "canceled"
            self._discard_outputs(job)
        else:
            job.state = "succeeded" if job.exit_code == 0 and job.error is None else "failed"
        for path in job.cleanup_files:
            path.unlink(missing_ok=True)
        self._emit(job, "done", self._done_payload(job))

    def _discard_outputs(self, job: JobRecord) -> None:
        """Remove what a canceled job had already written.

        ffmpeg and Remotion both write straight to the final path, so a canceled run
        leaves a truncated file there — which every `exists` check downstream would
        read as a finished export. Only files this job touched are removed, so
        cancelling a re-render never deletes the render it was replacing."""
        for path in job.outputs:
            try:
                if path.is_file() and path.stat().st_mtime >= job.started_at - 1:
                    path.unlink()
                    self._emit(job, "log", {"stream": "err", "line": f"discarded the partial {path.name}"})
            except OSError:
                pass

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
