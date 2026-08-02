"""Project state derived from the pipeline's on-disk filename conventions.

A "project" is a source sermon video; every artifact lives next to it as
`<stem>.segments.json`, `<stem>.highlights.json`, … The only fact that cannot
be derived from disk is which rendered clips belong to which sermon — that
link lives in a `<stem>.project.json` sidecar. Recently opened projects are
remembered in ~/.sermon/recents.json.
"""

import hashlib
import json
from pathlib import Path

from ..captions import APP_PUBLIC_DIR, load_cuts

RECENTS_FILE = Path.home() / ".sermon" / "recents.json"
RENDER_OUT_DIR = APP_PUBLIC_DIR.parent / "out"  # legacy: renders used to land inside the repo

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".mts", ".m2ts", ".webm"}


def path_id(path: Path) -> str:
    return hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:12]


def sidecar_path(video: Path) -> Path:
    return video.resolve().parent / f"{video.stem}.project.json"


def _load_sidecar(video: Path) -> dict:
    path = sidecar_path(video)
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"clips": []}


def register_clip(video: Path, clip: Path) -> None:
    data = _load_sidecar(video)
    clip_str = str(clip.resolve())
    if clip_str not in data["clips"]:
        data["clips"].append(clip_str)
    sidecar_path(video).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def load_recents() -> list[str]:
    if RECENTS_FILE.is_file():
        return json.loads(RECENTS_FILE.read_text(encoding="utf-8")).get("videos", [])
    return []


def remember_recent(video: Path) -> None:
    videos = [str(video.resolve())] + [v for v in load_recents() if v != str(video.resolve())]
    RECENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    RECENTS_FILE.write_text(json.dumps({"videos": videos[:20]}, indent=2) + "\n", encoding="utf-8")


def find_project_video(project_id: str) -> Path | None:
    for entry in load_recents():
        path = Path(entry)
        if path_id(path) == project_id and path.is_file():
            return path
    return None


def _probe_safe(video: Path) -> dict:
    from ..export import probe_video

    try:
        info = probe_video(video)
        return {
            "duration_sec": round(info["duration"], 3),
            "width": info["width"],
            "height": info["height"],
            "fps": round(float(info["fps"]), 3),
        }
    except Exception:
        return {"duration_sec": None, "width": None, "height": None, "fps": None}


def video_duration(video: Path) -> float | None:
    return _probe_safe(video)["duration_sec"]


def _artifact(path: Path) -> dict:
    exists = path.is_file()
    return {
        "path": str(path),
        "exists": exists,
        "mtime": path.stat().st_mtime if exists else None,
    }


def style_path(clip: Path) -> Path:
    """Per-clip caption style sidecar. Keys are camelCase because the Remotion
    composition reads this same file straight from captions/public/."""
    return clip.resolve().parent / f"{clip.stem}.style.json"


DEFAULT_CAPTION_STYLE = {"yOffset": 0}


def load_style(clip: Path) -> dict:
    for candidate in (style_path(clip), APP_PUBLIC_DIR / f"{clip.stem}.style.json"):
        if candidate.is_file():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                return {"yOffset": int(data.get("yOffset", 0))}
            except (json.JSONDecodeError, TypeError, ValueError):
                break  # unreadable sidecar — fall back to the default position
    return dict(DEFAULT_CAPTION_STYLE)


def artifact_paths(video: Path) -> dict[str, Path]:
    video = video.resolve()
    stem = video.stem
    d = video.parent
    return {
        "transcript": d / f"{stem}.transcript.txt",
        "srt": d / f"{stem}.srt",
        "segments": d / f"{stem}.segments.json",
        "highlights": d / f"{stem}.highlights.json",
        "highlights_md": d / f"{stem}.highlights.md",
        "resolve_xml": d / f"{stem}.resolve.xml",
    }


def output_dir(video: Path) -> Path:
    """Where this project's finished renders go.

    The sermon's own folder by default — everything else the pipeline produces
    already lives beside the video, and renders used to land inside the repo
    where nobody looks. A project may point somewhere else (see set_output_dir)."""
    configured = _load_sidecar(video).get("output_dir")
    if configured:
        path = Path(configured).expanduser()
        if path.is_dir():
            return path
    return video.resolve().parent


def set_output_dir(video: Path, directory: Path) -> Path:
    directory = directory.expanduser().resolve()
    data = _load_sidecar(video)
    data["output_dir"] = str(directory)
    sidecar_path(video).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return directory


def rendered_path(video: Path, clip: Path) -> Path:
    """Where this clip's finished render is, or will be written.

    New renders always go to the project's output folder, but an existing one is
    still shown where it actually sits: renders predating this setting live in
    captions/out, and pointing a project at a new folder must not make yesterday's
    render look like it was never made."""
    name = f"{clip.stem}.captioned.mp4"
    target = output_dir(video) / name
    previous = (video.resolve().parent / name, RENDER_OUT_DIR / name)
    if target.is_file():
        return target
    return next((p for p in previous if p.is_file()), target)


def clip_state(video: Path, clip: Path, probe: bool = True) -> dict:
    clip = clip.resolve()
    project_id = path_id(video)
    captions_json = clip.parent / f"{clip.stem}.captions.json"
    framing_json = clip.parent / f"{clip.stem}.framing.json"
    public_video = APP_PUBLIC_DIR / clip.name
    rendered = rendered_path(video, clip)
    in_public = public_video.is_file()

    # a render goes stale when any of its inputs (clip, captions, framing, caption
    # style — either copy) changed after it was produced; caption edits must visibly
    # propagate to the render step instead of silently showing an old file
    rendered_mtime = rendered.stat().st_mtime if rendered.is_file() else None
    sources = [clip, public_video, captions_json, framing_json, style_path(clip),
               clip.parent / f"{clip.stem}.cuts.json",
               APP_PUBLIC_DIR / f"{clip.stem}.captions.json",
               APP_PUBLIC_DIR / f"{clip.stem}.framing.json",
               APP_PUBLIC_DIR / f"{clip.stem}.style.json",
               APP_PUBLIC_DIR / f"{clip.stem}.cuts.json"]
    source_mtime = max((p.stat().st_mtime for p in sources if p.is_file()), default=None)
    stale = bool(rendered_mtime and source_mtime and source_mtime > rendered_mtime + 1)

    state = {
        "id": path_id(clip),
        "path": str(clip),
        "name": clip.name,
        "exists": clip.is_file(),
        "has_captions": captions_json.is_file(),
        "has_framing": framing_json.is_file() or (APP_PUBLIC_DIR / f"{clip.stem}.framing.json").is_file(),
        "has_corrections": (clip.parent / f"{clip.stem}.corrections.json").is_file(),
        "in_public": in_public,
        "style": load_style(clip),
        "cuts": load_cuts(clip),
        "rendered": {
            "path": str(rendered),
            "exists": rendered.is_file(),
            "mtime": rendered_mtime,
            "stale": stale,
        },
        "urls": {
            "video": f"/media/app/{clip.name}" if in_public else None,
            # resolved server-side from the ids: the render may sit anywhere the
            # project points, so there is no static mount to serve it from
            "rendered": f"/media/render/{project_id}/{path_id(clip)}" if rendered.is_file() else None,
        },
    }
    state.update(_probe_safe(clip) if (probe and clip.is_file()) else {"duration_sec": None, "width": None, "height": None, "fps": None})
    return state


def derive_state(video: Path, probe: bool = True) -> dict:
    video = video.resolve()
    arts = artifact_paths(video)
    clips = [clip_state(video, Path(c), probe=probe) for c in _load_sidecar(video)["clips"]]
    steps = {
        "transcribe": "done" if arts["segments"].is_file() else "pending",
        "highlights": "done" if arts["highlights"].is_file() else "pending",
        "export": "done" if arts["resolve_xml"].is_file() else "pending",
        "clips": "done" if clips else "pending",
        "captions": "done" if any(c["has_captions"] for c in clips) else "pending",
        "render": "done" if any(c["rendered"]["exists"] for c in clips) else "pending",
    }
    state = {
        "id": path_id(video),
        "video": {
            "path": str(video),
            "name": video.name,
            "exists": video.is_file(),
            **(_probe_safe(video) if (probe and video.is_file()) else {}),
        },
        "artifacts": {name: _artifact(path) for name, path in arts.items()},
        "output_dir": str(output_dir(video)),
        "clips": clips,
        "steps": steps,
    }
    return state
