"""JSON API routes for the sermon web UI."""

import asyncio
import json
import os
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import NamedTuple

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import __version__, layout
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
    # video | clip | folder — fixed prompts only, nothing user-supplied reaches AppleScript
    kind: str = "video"
    # a folder to open the dialog in (the highlight's 02_DAVINCI_EXPORT, usually)
    start_path: str | None = None


def _applescript_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


@router.post("/fs/pick")
def pick_file(req: PickFileRequest) -> dict:
    """Open a native macOS file dialog (the server runs on the Mac) and return the chosen path.

    Runs as a sync endpoint so FastAPI puts it on a worker thread — the dialog can
    stay open for minutes without blocking the event loop.
    """
    prompt = {
        "video": "Choose a sermon video",
        "clip": "Choose a rendered clip from Resolve",
        "folder": "Choose where finished videos should be saved",
    }.get(req.kind, "Choose a video")
    chooser = "choose folder" if req.kind == "folder" else "choose file"
    location = ""
    if req.start_path:
        start = Path(req.start_path).expanduser()
        if start.is_dir():
            location = f" default location (POSIX file {_applescript_string(str(start))})"
    script = f'POSIX path of ({chooser} with prompt "{prompt}"{location})'
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


def _highlight_dir(video: Path, index: int, title: str | None = None) -> Path:
    """The folder for highlight `index`, taking its name from the highlights file
    when nothing is on disk yet."""
    existing = layout.highlight_dir(video, index)
    if existing is not None:
        return existing
    if title is None:
        path = projects.artifact_paths(video)["highlights"]
        if not path.is_file():
            raise HTTPException(422, "no highlights yet — run the highlights step first")
        items = json.loads(path.read_text(encoding="utf-8")).get("highlights", [])
        if not 1 <= index <= len(items):
            raise HTTPException(404, f"highlight {index} does not exist")
        title = items[index - 1]["title"]
    return layout.highlight_dir(video, index, title)


def _highlight_folders(video: Path, index: int, title: str) -> dict:
    """The folder set the UI shows for one highlight: where its export went, where
    the DaVinci render is expected, and where the finished video lands."""
    highlight = _highlight_dir(video, index, title)
    vertical = layout.vertical_clip(highlight)
    final = layout.final_render(highlight)
    return {
        "folder": str(highlight),
        "davinci_dir": str(layout.davinci_dir(highlight)),
        "vertical": {"index": index, "path": str(vertical), "exists": vertical.is_file()},
        "final": {"path": str(final), "exists": final.is_file()},
    }


@router.get("/projects/{project_id}/highlights")
def get_highlights(project_id: str) -> dict:
    video = _project_video(project_id)
    highlights = projects.artifact_paths(video)["highlights"]
    if not highlights.is_file():
        raise HTTPException(404, "no highlights yet")
    data = json.loads(highlights.read_text(encoding="utf-8"))
    for i, h in enumerate(data.get("highlights", []), 1):
        h.update(_highlight_folders(video, i, h["title"]))
    return data


class HighlightPatchRequest(BaseModel):
    """What the UI may edit on one highlight. An omitted field is left alone; these
    are separate PATCHes in practice, not one form."""

    end_sec: float | None = None
    # normalized x [0..1] of the person the vertical crop should follow, for footage
    # with several people in frame (see ../track.py). An explicit null clears the
    # pick and tracking goes back to choosing the largest face itself.
    subject_x: float | None = Field(None, ge=0.0, le=1.0)
    # give the frame to whoever is talking instead, cutting between them (../speakers.py).
    # The alternative to subject_x, so setting either one clears the other.
    follow_speaker: bool | None = None


MIN_HIGHLIGHT_SEC = 5.0


@router.patch("/projects/{project_id}/highlights/{index}")
def update_highlight(project_id: str, index: int, req: HighlightPatchRequest) -> dict:
    """Edit one highlight in place (1-based index, as ranked in the file).

    The out point, because Gemini picks the end from the transcript alone and so
    regularly lands a beat early or late — the length is really only judgeable
    while watching. The subject, because a landscape frame may hold four people and
    only the viewer knows which one the 9:16 crop should follow.

    Rewriting the highlights file means the vertical export, the Resolve XML and
    the markdown notes all follow one edit."""
    from ..highlights import write_highlights
    from ..transcribe import format_timestamp

    video = _project_video(project_id)
    path = projects.artifact_paths(video)["highlights"]
    if not path.is_file():
        raise HTTPException(404, "no highlights yet")
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("highlights", [])
    if not 1 <= index <= len(items):
        raise HTTPException(404, f"highlight {index} does not exist")

    highlight = items[index - 1]
    sent = req.model_fields_set  # "cleared" and "not mentioned" are different asks

    if req.end_sec is not None:
        end = float(req.end_sec)
        ceiling = projects.video_duration(video)
        if ceiling is not None:
            end = min(end, ceiling)
        end = max(end, highlight["start_sec"] + MIN_HIGHLIGHT_SEC)
        highlight["end_sec"] = round(end, 3)
        highlight["end"] = format_timestamp(end)
        highlight["duration_sec"] = round(end - highlight["start_sec"], 1)

        # the excerpt is the transcript of the range, so it has to follow the edit
        segments_file = projects.artifact_paths(video)["segments"]
        if segments_file.is_file():
            segments = json.loads(segments_file.read_text(encoding="utf-8"))["segments"]
            highlight["excerpt"] = " ".join(
                s["text"] for s in segments
                if s["start"] >= highlight["start_sec"] - 0.5 and s["end"] <= end + 0.5
            )

    # the two framing modes are alternatives, so whichever is set turns the other off
    if "subject_x" in sent:
        if req.subject_x is None:
            highlight.pop("subject_x", None)
        else:
            highlight["subject_x"] = round(float(req.subject_x), 5)
            highlight.pop("follow_speaker", None)
    if "follow_speaker" in sent:
        if req.follow_speaker:
            highlight["follow_speaker"] = True
            highlight.pop("subject_x", None)
        else:
            highlight.pop("follow_speaker", None)

    write_highlights(
        items,
        data.get("video", video.name),
        video.stem,
        layout.source_dir(video),
        data.get("gemini_model", ""),
    )
    # attached after the write — derived fields, not part of the file
    return {**highlight, **_highlight_folders(video, index, highlight["title"])}


class OutputDirRequest(BaseModel):
    path: str


@router.post("/projects/{project_id}/output-dir")
def set_output_dir(project_id: str, req: OutputDirRequest) -> dict:
    """Choose where this project's finished renders are written."""
    video = _project_video(project_id)
    directory = Path(req.path).expanduser()
    if not directory.is_dir():
        raise HTTPException(422, f"{directory} is not a folder")
    if not os.access(directory, os.W_OK):
        raise HTTPException(422, f"{directory} is not writable")
    return {"output_dir": str(projects.set_output_dir(video, directory))}


@router.delete("/projects/{project_id}/output-dir")
def clear_output_dir(project_id: str) -> dict:
    """Back to the default: each finished video in its own highlight folder."""
    projects.set_output_dir(_project_video(project_id), None)
    return {"output_dir": None}


@router.post("/projects/{project_id}/export")
def export_resolve_xml(project_id: str) -> dict:
    from ..export import export_timeline

    video = _project_video(project_id)
    highlights = projects.artifact_paths(video)["highlights"]
    if not highlights.is_file():
        raise HTTPException(422, "no highlights yet — run the highlights step first")
    # Resolve links whatever this XML names, and it cannot open a .mkv — export_timeline
    # picks the remuxed twin up on its own. It would also *build* one, and that would
    # block this request for as long as the copy takes, so an unconverted sermon is sent
    # back to the button that does it with a progress bar instead.
    if not projects.playable_source(video).is_file():
        raise HTTPException(422, f"{video.name} needs a Resolve-readable copy first — convert it on the Highlights step")
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
    return projects.clip_state(video, clip)


@router.get("/projects/{project_id}/clips/{clip_id}")
def get_clip(project_id: str, clip_id: str) -> dict:
    video = _project_video(project_id)
    return projects.clip_state(video, _project_clip(project_id, clip_id))


def _write_sidecar(clip: Path, suffix: str, contents: str) -> list[str]:
    """Write a clip sidecar and the copy the Remotion app reads.

    Keeping both identical is what makes Studio, the web Player and the render
    agree — they each read whichever copy is closest to them."""
    local = layout.ensure_parent(layout.sidecar(clip, suffix))
    local.write_text(contents, encoding="utf-8")
    written = [str(local)]
    if APP_PUBLIC_DIR.is_dir():
        public = APP_PUBLIC_DIR / layout.public_sidecar_name(clip, suffix)
        public.write_text(contents, encoding="utf-8")
        written.append(str(public))
    return written


@router.get("/projects/{project_id}/clips/{clip_id}/captions")
def get_captions(project_id: str, clip_id: str) -> list:
    clip = _project_clip(project_id, clip_id)
    captions_json = layout.sidecar(clip, "captions.json")
    if not captions_json.is_file():
        raise HTTPException(404, "no captions yet — run the captions step first")
    return json.loads(captions_json.read_text(encoding="utf-8"))


@router.put("/projects/{project_id}/clips/{clip_id}/captions")
def put_captions(project_id: str, clip_id: str, captions: list[dict]) -> dict:
    clip = _project_clip(project_id, clip_id)
    contents = json.dumps(captions, indent=2, ensure_ascii=False) + "\n"
    return {"ok": True, "written": _write_sidecar(clip, "captions.json", contents)}


@router.get("/projects/{project_id}/clips/{clip_id}/corrections")
def get_corrections(project_id: str, clip_id: str) -> dict:
    clip = _project_clip(project_id, clip_id)
    corr_path = layout.sidecar(clip, "corrections.json")
    if not corr_path.is_file():
        raise HTTPException(404, "no proofread results stored for this clip")
    return json.loads(corr_path.read_text(encoding="utf-8"))


class CaptionStyleRequest(BaseModel):
    # camelCase mirrors the Remotion props — the composition reads this file itself
    yOffset: int = Field(0, ge=-400, le=1000)
    # non-empty turns on the intro graphics (bottom blur, name, logo)
    speakerName: str = Field("", max_length=80)


@router.get("/projects/{project_id}/clips/{clip_id}/style")
def get_style(project_id: str, clip_id: str) -> dict:
    """Caption style for this clip — defaults when no sidecar was written yet."""
    return projects.load_style(_project_clip(project_id, clip_id))


@router.put("/projects/{project_id}/clips/{clip_id}/style")
def put_style(project_id: str, clip_id: str, style: CaptionStyleRequest) -> dict:
    clip = _project_clip(project_id, clip_id)
    saved = {"yOffset": style.yOffset, "speakerName": style.speakerName.strip()}
    contents = json.dumps(saved, indent=2, ensure_ascii=False) + "\n"
    return {"ok": True, "style": saved, "written": _write_sidecar(clip, "style.json", contents)}


@router.get("/projects/{project_id}/clips/{clip_id}/framing")
def get_framing(project_id: str, clip_id: str) -> dict:
    clip = _project_clip(project_id, clip_id)
    framing = layout.sidecar(clip, "framing.json")
    if not framing.is_file():
        raise HTTPException(404, "no framing data — run tracking first")
    return json.loads(framing.read_text(encoding="utf-8"))


# --- jobs --------------------------------------------------------------------


class JobRequest(BaseModel):
    kind: str
    project_id: str | None = None
    clip_id: str | None = None
    params: dict = Field(default_factory=dict)


def _require_gemini_key() -> None:
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        raise HTTPException(422, "GEMINI_API_KEY is not set — add it to the .env file at the repo root")


class JobSpec(NamedTuple):
    argv: list[str]
    cwd: Path
    preset_result: dict | None = None
    cleanup_files: list[Path] = []
    # files this job writes in place — removed again if it is canceled part-way
    outputs: list[Path] = []


def _build_job(req: JobRequest) -> JobSpec:
    p = req.params
    repo_cwd = APP_PUBLIC_DIR.parents[1]

    if req.kind == "convert":
        video = _project_video(req.project_id)
        out = projects.playable_source(video)
        if out == video:
            raise HTTPException(422, f"{video.name} is already playable — nothing to convert")
        argv = worker_argv("convert", "--video", str(video), "--out", str(out))
        return JobSpec(argv, repo_cwd, {"paths": {"playable": str(out)}}, outputs=[out])

    if req.kind == "transcribe":
        video = _project_video(req.project_id)
        argv = worker_argv(
            "transcribe", "--video", str(video),
            "--model", p.get("model", "large-v3-turbo"), "--language", p.get("language", "sk"),
        )
        return JobSpec(argv, repo_cwd)

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
            "--max-duration", str(p.get("max_duration", 100)),
            "--gemini-model", p.get("gemini_model", "gemini-flash-latest"),
        )
        return JobSpec(argv, repo_cwd)

    if req.kind == "export_vertical":
        video = _project_video(req.project_id)
        try:
            start, end, index = float(p["start_sec"]), float(p["end_sec"]), int(p["index"])
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(422, "export_vertical needs start_sec, end_sec and index") from exc
        # all three stage folders now, not just the one being written: the user has to
        # be able to save their DaVinci render into 02_DAVINCI_EXPORT
        highlight = layout.ensure_highlight_dirs(_highlight_dir(video, index, p.get("title")))
        out = layout.vertical_clip(highlight)
        argv = worker_argv(
            "export-vertical", "--video", str(video),
            "--start", str(start), "--end", str(end), "--out", str(out),
        )
        # optional: open the clip with its hook moment instead of playing the
        # passage straight through (the Resolve XML lays the same shape out)
        hook_start, hook_end = p.get("hook_start_sec"), p.get("hook_end_sec")
        if hook_start is not None and hook_end is not None:
            argv += ["--hook-start", str(float(hook_start)), "--hook-end", str(float(hook_end))]
        # optional: follow the person picked in the preview rather than the largest face,
        # or hand the frame to whoever is talking and cut between them
        if p.get("subject_x") is not None:
            try:
                subject_x = float(p["subject_x"])
            except (TypeError, ValueError) as exc:
                raise HTTPException(422, "subject_x must be a number between 0 and 1") from exc
            if not 0.0 <= subject_x <= 1.0:
                raise HTTPException(422, "subject_x must be a number between 0 and 1")
            argv += ["--subject-x", f"{subject_x:.5f}"]
        elif p.get("follow_speaker"):
            argv.append("--follow-speaker")
        return JobSpec(argv, repo_cwd, {"paths": {"vertical": str(out)}}, outputs=[out])

    if req.kind in ("captions", "track"):
        clip = _project_clip(req.project_id, req.clip_id)
        if req.kind == "track":
            return JobSpec(worker_argv("track", "--clip", str(clip)), repo_cwd)
        argv = worker_argv(
            "captions", "--clip", str(clip),
            "--model", p.get("model", "large-v3-turbo"), "--language", p.get("language", "sk"),
            "--gemini-model", p.get("gemini_model", "gemini-flash-latest"),
        )
        if not p.get("proofread", True):
            argv.append("--no-proofread")
        return JobSpec(argv, repo_cwd)

    if req.kind == "render":
        video = _project_video(req.project_id)
        clip = _project_clip(req.project_id, req.clip_id)
        public_src = layout.public_name(clip)
        if not (APP_PUBLIC_DIR / public_src).is_file() or not (
            APP_PUBLIC_DIR / layout.public_sidecar_name(clip, "captions.json")
        ).is_file():
            raise HTTPException(422, "clip is not in captions/public yet — run the captions step first")
        out_path = layout.ensure_parent(projects.rendered_path(video, clip))
        props_file = Path(tempfile.gettempdir()) / f"sermon-props-{uuid.uuid4().hex[:8]}.json"
        style = projects.load_style(clip)
        props = {
            "src": public_src,
            "captions": None,
            "framing": None,
            "yOffset": style["yOffset"],
            "cuts": projects.load_cuts(clip) or None,
            "speakerName": style["speakerName"] or None,
        }
        props_file.write_text(json.dumps(props), encoding="utf-8")
        argv = ["npx", "remotion", "render", "CaptionedClip", f"--props={props_file}", str(out_path)]
        return JobSpec(argv, APP_PUBLIC_DIR.parent, {"paths": {"output": str(out_path)}},
                       cleanup_files=[props_file], outputs=[out_path])

    raise HTTPException(422, f"unknown job kind: {req.kind}")


@router.post("/jobs")
async def start_job(req: JobRequest) -> dict:
    spec = _build_job(req)
    try:
        job = await manager.start(req.kind, spec.argv, spec.cwd,
                                  project_id=req.project_id, clip_id=req.clip_id)
    except JobBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    if spec.preset_result is not None:
        job.result = spec.preset_result
    job.cleanup_files.extend(spec.cleanup_files)
    job.outputs.extend(spec.outputs)
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


@media_router.get("/render/{project_id}/{clip_id}")
def render_media(project_id: str, clip_id: str):
    """The finished render, resolved from the ids — it may live outside any mount."""
    from fastapi.responses import FileResponse

    video = _project_video(project_id)
    target = projects.rendered_path(video, _project_clip(project_id, clip_id))
    if not target.is_file():
        raise HTTPException(404, "not rendered yet")
    return FileResponse(target)


@media_router.get("/source/{project_id}/{filename}")
def source_media(project_id: str, filename: str):
    """The sermon, in whatever form the browser can actually play it.

    Which file that is gets resolved server-side, not from `filename`: a `.mkv` is
    served through its remuxed twin in `00_SOURCE/`, a path the client never sees.
    The name is still in the URL because a media URL has to end in a real
    extension — `<video>`, the Remotion player, the network panel and "save as"
    all read the type off the path, and only some of them fall back to the
    Content-Type header."""
    from fastapi.responses import FileResponse

    video = _project_video(project_id)
    target = projects.playable_source(video)
    if not target.is_file():
        raise HTTPException(404, "no browser-playable copy of this video yet")
    return FileResponse(target, filename=target.name, content_disposition_type="inline")
