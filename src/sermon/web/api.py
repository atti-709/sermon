"""JSON API routes for the sermon web UI."""

import asyncio
import json
import os
import subprocess
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import __version__
from ..captions import APP_PUBLIC_DIR
from . import projects
from .jobs import JobBusy, manager, worker_argv

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {"ok": True, "version": __version__}


@router.get("/gemini-status")
def gemini_status() -> dict:
    return {"key_present": bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))}


# --- filesystem browser ------------------------------------------------------


@router.get("/fs/list")
def fs_list(path: str | None = None) -> dict:
    base = Path(path).expanduser() if path else Path.home() / "Movies"
    if not base.is_dir():
        base = Path.home()
    base = base.resolve()
    home = Path.home().resolve()
    if base != home and home not in base.parents:
        raise HTTPException(403, "browsing outside the home directory is not allowed")

    entries = []
    for child in sorted(base.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if child.name.startswith("."):
            continue
        is_dir = child.is_dir()
        if not is_dir and child.suffix.lower() not in projects.VIDEO_EXTENSIONS:
            continue
        stat = child.stat()
        entries.append(
            {
                "name": child.name,
                "path": str(child),
                "is_dir": is_dir,
                "size": None if is_dir else stat.st_size,
                "mtime": stat.st_mtime,
            }
        )
    parent = str(base.parent) if base != home else None
    return {"path": str(base), "parent": parent, "entries": entries}


class PickFileRequest(BaseModel):
    kind: str = "video"  # video | clip — fixed prompts only, nothing user-supplied reaches AppleScript


@router.post("/fs/pick")
def pick_file(req: PickFileRequest) -> dict:
    """Open a native macOS file dialog (the server runs on the Mac) and return the chosen path.

    Runs as a sync endpoint so FastAPI puts it on a worker thread — the dialog can
    stay open for minutes without blocking the event loop.
    """
    prompt = {
        "video": "Choose a sermon video",
        "clip": "Choose a rendered clip from Resolve",
    }.get(req.kind, "Choose a video")
    script = f'POSIX path of (choose file with prompt "{prompt}")'
    try:
        proc = subprocess.run(
            ["osascript", "-e", "activate", "-e", script],
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        return {"path": None}
    if proc.returncode != 0:  # user canceled the dialog
        return {"path": None}
    return {"path": proc.stdout.strip() or None}


# --- projects ----------------------------------------------------------------


class ProjectRequest(BaseModel):
    video_path: str


def _project_video(project_id: str) -> Path:
    video = projects.find_project_video(project_id)
    if video is None:
        raise HTTPException(404, "unknown project — open its video again")
    return video


@router.post("/projects")
def create_project(req: ProjectRequest) -> dict:
    video = Path(req.video_path).expanduser().resolve()
    if not video.is_file():
        raise HTTPException(422, f"{video} does not exist")
    if video.suffix.lower() not in projects.VIDEO_EXTENSIONS:
        raise HTTPException(422, f"{video.name} is not a supported video file")
    projects.remember_recent(video)
    return projects.derive_state(video)


@router.get("/projects")
def list_projects() -> dict:
    result = []
    for entry in projects.load_recents():
        video = Path(entry)
        if video.is_file():
            result.append(projects.derive_state(video, probe=False))
    return {"projects": result}


@router.get("/projects/{project_id}")
def get_project(project_id: str) -> dict:
    return projects.derive_state(_project_video(project_id))


@router.get("/projects/{project_id}/transcript")
def get_transcript(project_id: str) -> dict:
    video = _project_video(project_id)
    segments = projects.artifact_paths(video)["segments"]
    if not segments.is_file():
        raise HTTPException(404, "not transcribed yet")
    return json.loads(segments.read_text(encoding="utf-8"))


def vertical_clip_path(video: Path, index: int) -> Path:
    return video.resolve().parent / f"{video.stem}.h{index}.vertical.mov"


@router.get("/projects/{project_id}/highlights")
def get_highlights(project_id: str) -> dict:
    video = _project_video(project_id)
    highlights = projects.artifact_paths(video)["highlights"]
    if not highlights.is_file():
        raise HTTPException(404, "no highlights yet")
    data = json.loads(highlights.read_text(encoding="utf-8"))
    for i, h in enumerate(data.get("highlights", []), 1):
        out = vertical_clip_path(video, i)
        h["vertical"] = {"index": i, "path": str(out), "exists": out.is_file()}
    return data


@router.post("/projects/{project_id}/export")
def export_resolve_xml(project_id: str) -> dict:
    from ..export import export_timeline

    video = _project_video(project_id)
    highlights = projects.artifact_paths(video)["highlights"]
    if not highlights.is_file():
        raise HTTPException(422, "no highlights yet — run the highlights step first")
    try:
        xml_path = export_timeline(highlights, video)
    except BaseException as exc:  # export_timeline raises SystemExit on empty highlights
        raise HTTPException(422, f"export failed: {exc}") from exc
    return {"xml_path": str(xml_path)}


# --- clips -------------------------------------------------------------------


class ClipRequest(BaseModel):
    clip_path: str


def _project_clip(project_id: str, clip_id: str) -> Path:
    video = _project_video(project_id)
    for state in projects.derive_state(video, probe=False)["clips"]:
        if state["id"] == clip_id:
            return Path(state["path"])
    raise HTTPException(404, "unknown clip")


@router.post("/projects/{project_id}/clips")
def add_clip(project_id: str, req: ClipRequest) -> dict:
    video = _project_video(project_id)
    clip = Path(req.clip_path).expanduser().resolve()
    if not clip.is_file():
        raise HTTPException(422, f"{clip} does not exist")
    if clip.suffix.lower() not in projects.VIDEO_EXTENSIONS:
        raise HTTPException(422, f"{clip.name} is not a supported video file")
    projects.register_clip(video, clip)
    return projects.clip_state(project_id, clip)


@router.get("/projects/{project_id}/clips/{clip_id}")
def get_clip(project_id: str, clip_id: str) -> dict:
    return projects.clip_state(project_id, _project_clip(project_id, clip_id))


@router.get("/projects/{project_id}/clips/{clip_id}/captions")
def get_captions(project_id: str, clip_id: str) -> list:
    clip = _project_clip(project_id, clip_id)
    captions_json = clip.parent / f"{clip.stem}.captions.json"
    if not captions_json.is_file():
        raise HTTPException(404, "no captions yet — run the captions step first")
    return json.loads(captions_json.read_text(encoding="utf-8"))


@router.put("/projects/{project_id}/clips/{clip_id}/captions")
def put_captions(project_id: str, clip_id: str, captions: list[dict]) -> dict:
    clip = _project_clip(project_id, clip_id)
    contents = json.dumps(captions, indent=2, ensure_ascii=False) + "\n"
    # source of truth next to the clip, plus the copy the Remotion app reads —
    # keeping both identical means Studio, Player and render never disagree
    written = []
    for target in (clip.parent / f"{clip.stem}.captions.json", APP_PUBLIC_DIR / f"{clip.stem}.captions.json"):
        if target.parent.is_dir():
            target.write_text(contents, encoding="utf-8")
            written.append(str(target))
    return {"ok": True, "written": written}


@router.get("/projects/{project_id}/clips/{clip_id}/corrections")
def get_corrections(project_id: str, clip_id: str) -> dict:
    clip = _project_clip(project_id, clip_id)
    corr_path = clip.parent / f"{clip.stem}.corrections.json"
    if not corr_path.is_file():
        raise HTTPException(404, "no proofread results stored for this clip")
    return json.loads(corr_path.read_text(encoding="utf-8"))


class CaptionStyleRequest(BaseModel):
    # camelCase mirrors the Remotion prop — the composition reads this file itself
    yOffset: int = Field(0, ge=-400, le=1000)


@router.get("/projects/{project_id}/clips/{clip_id}/style")
def get_style(project_id: str, clip_id: str) -> dict:
    """Caption style for this clip — defaults when no sidecar was written yet."""
    return projects.load_style(_project_clip(project_id, clip_id))


@router.put("/projects/{project_id}/clips/{clip_id}/style")
def put_style(project_id: str, clip_id: str, style: CaptionStyleRequest) -> dict:
    clip = _project_clip(project_id, clip_id)
    saved = {"yOffset": style.yOffset}
    contents = json.dumps(saved, indent=2) + "\n"
    # both copies stay identical, as for captions — Studio, Player and render
    # must never disagree about where the captions sit
    written = []
    for target in (projects.style_path(clip), APP_PUBLIC_DIR / f"{clip.stem}.style.json"):
        if target.parent.is_dir():
            target.write_text(contents, encoding="utf-8")
            written.append(str(target))
    return {"ok": True, "style": saved, "written": written}


@router.get("/projects/{project_id}/clips/{clip_id}/framing")
def get_framing(project_id: str, clip_id: str) -> dict:
    clip = _project_clip(project_id, clip_id)
    for candidate in (clip.parent / f"{clip.stem}.framing.json", APP_PUBLIC_DIR / f"{clip.stem}.framing.json"):
        if candidate.is_file():
            return json.loads(candidate.read_text(encoding="utf-8"))
    raise HTTPException(404, "no framing data — run tracking first")


# --- jobs --------------------------------------------------------------------


class JobRequest(BaseModel):
    kind: str
    project_id: str | None = None
    clip_id: str | None = None
    params: dict = Field(default_factory=dict)


def _require_gemini_key() -> None:
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        raise HTTPException(422, "GEMINI_API_KEY is not set — add it to the .env file at the repo root")


def _build_job(req: JobRequest) -> tuple[list[str], Path, dict | None, list[Path]]:
    """Returns (argv, cwd, preset_result, cleanup_files) for the requested job."""
    p = req.params
    repo_cwd = APP_PUBLIC_DIR.parents[1]

    if req.kind == "transcribe":
        video = _project_video(req.project_id)
        argv = worker_argv(
            "transcribe", "--video", str(video),
            "--model", p.get("model", "large-v3-turbo"), "--language", p.get("language", "sk"),
        )
        return argv, repo_cwd, None, []

    if req.kind == "highlights":
        _require_gemini_key()
        video = _project_video(req.project_id)
        segments = projects.artifact_paths(video)["segments"]
        if not segments.is_file():
            raise HTTPException(422, "not transcribed yet — run the transcribe step first")
        argv = worker_argv(
            "highlights", "--segments", str(segments),
            "--count", str(p.get("count", 8)),
            "--min-duration", str(p.get("min_duration", 20)),
            "--max-duration", str(p.get("max_duration", 110)),
            "--gemini-model", p.get("gemini_model", "gemini-flash-latest"),
        )
        return argv, repo_cwd, None, []

    if req.kind == "export_vertical":
        video = _project_video(req.project_id)
        try:
            start, end, index = float(p["start_sec"]), float(p["end_sec"]), int(p["index"])
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(422, "export_vertical needs start_sec, end_sec and index") from exc
        out = vertical_clip_path(video, index)
        argv = worker_argv(
            "export-vertical", "--video", str(video),
            "--start", str(start), "--end", str(end), "--out", str(out),
        )
        # optional: open the clip with its hook moment instead of playing the
        # passage straight through (the Resolve XML lays the same shape out)
        hook_start, hook_end = p.get("hook_start_sec"), p.get("hook_end_sec")
        if hook_start is not None and hook_end is not None:
            argv += ["--hook-start", str(float(hook_start)), "--hook-end", str(float(hook_end))]
        return argv, repo_cwd, {"paths": {"vertical": str(out)}}, []

    if req.kind in ("captions", "track"):
        clip = _project_clip(req.project_id, req.clip_id)
        if req.kind == "track":
            return worker_argv("track", "--clip", str(clip)), repo_cwd, None, []
        argv = worker_argv(
            "captions", "--clip", str(clip),
            "--model", p.get("model", "large-v3-turbo"), "--language", p.get("language", "sk"),
            "--gemini-model", p.get("gemini_model", "gemini-flash-latest"),
        )
        if not p.get("proofread", True):
            argv.append("--no-proofread")
        return argv, repo_cwd, None, []

    if req.kind == "render":
        clip = _project_clip(req.project_id, req.clip_id)
        if not (APP_PUBLIC_DIR / clip.name).is_file() or not (APP_PUBLIC_DIR / f"{clip.stem}.captions.json").is_file():
            raise HTTPException(422, "clip is not in captions/public yet — run the captions step first")
        projects.RENDER_OUT_DIR.mkdir(parents=True, exist_ok=True)
        props_file = projects.RENDER_OUT_DIR / f".props-{uuid.uuid4().hex[:8]}.json"
        props = {
            "src": clip.name,
            "captions": None,
            "framing": None,
            "yOffset": projects.load_style(clip)["yOffset"],
        }
        props_file.write_text(json.dumps(props), encoding="utf-8")
        out_path = projects.RENDER_OUT_DIR / f"{clip.stem}.captioned.mp4"
        argv = ["npx", "remotion", "render", "CaptionedClip", f"--props={props_file}", str(out_path)]
        return argv, APP_PUBLIC_DIR.parent, {"paths": {"output": str(out_path)}}, [props_file]

    raise HTTPException(422, f"unknown job kind: {req.kind}")


@router.post("/jobs")
async def start_job(req: JobRequest) -> dict:
    argv, cwd, preset_result, cleanup = _build_job(req)
    try:
        job = await manager.start(req.kind, argv, cwd, project_id=req.project_id, clip_id=req.clip_id)
    except JobBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    if preset_result is not None:
        job.result = preset_result
    job.cleanup_files.extend(cleanup)
    return {"job_id": job.id}


@router.get("/jobs/current")
def get_current_job() -> dict:
    """The running job, if any — lets the UI re-attach after tab navigation."""
    job = manager.current
    return {"job": job.snapshot() if job else None}


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown job")
    return job.snapshot()


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request) -> StreamingResponse:
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown job")
    try:
        last_seq = int(request.headers.get("last-event-id", -1))
    except ValueError:
        last_seq = -1

    async def stream():
        queue = manager.subscribe(job, last_seq=last_seq)
        try:
            while True:
                try:
                    seq, name, payload = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield f"id: {seq}\nevent: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                if name == "done":
                    return
        finally:
            manager.unsubscribe(job, queue)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str) -> dict:
    await manager.cancel(job_id)
    return {"ok": True}


# --- misc --------------------------------------------------------------------


class RevealRequest(BaseModel):
    path: str


@router.post("/reveal")
def reveal_in_finder(req: RevealRequest) -> dict:
    path = Path(req.path).expanduser()
    if not path.exists():
        raise HTTPException(404, f"{path} does not exist")
    subprocess.run(["open", "-R", str(path)], check=False)
    return {"ok": True}


# --- project media (source video preview) ------------------------------------

media_router = APIRouter()


@media_router.get("/project/{project_id}/{filename}")
def project_media(project_id: str, filename: str):
    from fastapi.responses import FileResponse

    video = _project_video(project_id)
    target = (video.parent / filename).resolve()
    if target.parent != video.parent.resolve() or target.suffix.lower() not in projects.VIDEO_EXTENSIONS:
        raise HTTPException(403, "not a project media file")
    if not target.is_file():
        raise HTTPException(404, f"{filename} not found")
    return FileResponse(target)
