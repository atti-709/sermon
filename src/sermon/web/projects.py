"""Project state derived from the pipeline's on-disk layout (see ../layout.py).

A "project" is a source sermon video; whole-sermon artifacts live in its
`00_SOURCE/` folder and each highlight owns a numbered folder of its own. The one
fact no folder can carry is which rendered clips the user registered for this
sermon — that link lives in `00_SOURCE/<stem>.project.json`. Recently opened
projects are remembered in ~/.sermon/recents.json.
"""

import hashlib
import json
from pathlib import Path

from .. import layout, playable
from ..captions import APP_PUBLIC_DIR, load_cuts

RECENTS_FILE = Path.home() / ".sermon" / "recents.json"

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".mts", ".m2ts", ".webm"}


def path_id(path: Path) -> str:
    return hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:12]


def sidecar_path(video: Path) -> Path:
    return layout.source_file(video, "project.json")


def _load_sidecar(video: Path) -> dict:
    path = sidecar_path(video)
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"clips": []}


def _save_sidecar(video: Path, data: dict) -> None:
    layout.ensure_parent(sidecar_path(video)).write_text(
        json.dumps(data, indent=2) + "\n", encoding="utf-8"
    )


def register_clip(video: Path, clip: Path) -> None:
    data = _load_sidecar(video)
    clip_str = str(clip.resolve())
    if clip_str not in data["clips"]:
        data["clips"].append(clip_str)
    _save_sidecar(video, data)


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


def playable_source(video: Path) -> Path:
    """The file the browser previews and the Resolve XML links.

    The sermon itself whenever a browser can open it, and its remuxed `00_SOURCE/`
    twin when it cannot — a `.mkv` plays back through nothing in this app, however
    ordinary the h264 inside it is (see ../playable.py)."""
    return video if playable.is_playable(video) else layout.playable_source(video)


def _playable_state(video: Path, probe: bool) -> dict:
    """Whether this sermon can be previewed, and whether making it so needs a job.

    Skipped along with the ffprobe when the caller only wants a cheap listing —
    the recents screen shows no video."""
    if not probe or not video.is_file():
        return {"needs_conversion": False, "playable": True}
    target = playable_source(video)
    return {"needs_conversion": target != video, "playable": target.is_file()}


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
    return layout.sidecar(clip, "style.json")


DEFAULT_CAPTION_STYLE = {"yOffset": 0, "speakerName": ""}

# the intro card holds one line of a name; the API caps the field at the same length
MAX_SPEAKER_LEN = 80
MAX_SPEAKERS = 8


def load_speakers(sermon_dir: Path | None) -> list[str]:
    """The names on this sermon folder's `_SPEAKERS.txt`, in programme order.

    One name per line, `#` comments and blank lines ignored — a format someone can
    fix in TextEdit on the NAS without breaking anything. A folder with no such
    file, or none the app was pointed inside, simply has no names to offer."""
    if sermon_dir is None:
        return []
    path = layout.speakers_file(sermon_dir)
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    names: list[str] = []
    for line in lines:
        name = line.split("#", 1)[0].strip()
        if name and name not in names:
            names.append(name[:MAX_SPEAKER_LEN])
    return names[:MAX_SPEAKERS]


def load_style(clip: Path) -> dict:
    path = style_path(clip)
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return {
                "yOffset": int(data.get("yOffset", 0)),
                "speakerName": str(data.get("speakerName", "") or ""),
            }
        except (json.JSONDecodeError, TypeError, ValueError):
            pass  # unreadable sidecar — fall back to the defaults
    # Nothing saved for this clip yet, so the programme's first name pre-fills it and
    # the clip arrives with its intro already on. Only in this branch: once a sidecar
    # exists an empty name is the user having deliberately turned the intro off, and
    # re-filling it from the folder would overrule them on every reload.
    speakers = load_speakers(layout.sermon_dir_for_clip(clip))
    return {**DEFAULT_CAPTION_STYLE, "speakerName": speakers[0] if speakers else ""}


def artifact_paths(video: Path) -> dict[str, Path]:
    return {
        "transcript": layout.source_file(video, "transcript.txt"),
        "srt": layout.source_file(video, "srt"),
        "segments": layout.source_file(video, "segments.json"),
        "highlights": layout.source_file(video, "highlights.json"),
        "highlights_md": layout.source_file(video, "highlights.md"),
        "resolve_xml": layout.source_file(video, "resolve.xml"),
    }


def output_dir(video: Path) -> Path | None:
    """An explicit folder this project's renders are written to, if one was chosen.

    None means the default: each finished video lands in its own highlight folder,
    next to the stages it came out of."""
    configured = _load_sidecar(video).get("output_dir")
    if configured:
        path = Path(configured).expanduser()
        if path.is_dir():
            return path
    return None


def set_output_dir(video: Path, directory: Path | None) -> Path | None:
    """Point renders at `directory`, or back at the highlight folders with None."""
    data = _load_sidecar(video)
    if directory is None:
        data.pop("output_dir", None)
    else:
        directory = directory.expanduser().resolve()
        data["output_dir"] = str(directory)
    _save_sidecar(video, data)
    return directory


def rendered_path(video: Path, clip: Path) -> Path:
    """Where this clip's finished render is, or will be written.

    Its highlight's own folder, named after the highlight — the folder already says
    which sermon and which moment, so the file needs no other qualifier. A project
    pointed at an explicit output folder writes there instead."""
    highlight = layout.highlight_dir_for_clip(clip)
    configured = output_dir(video)
    if configured is not None:
        name = f"{layout.highlight_title(highlight)}.mp4" if highlight else f"{clip.stem}.captioned.mp4"
        return configured / name
    if highlight is not None:
        return layout.final_render(highlight)
    # a clip registered from outside the layout keeps its render beside itself
    return clip.resolve().parent / f"{clip.stem}.captioned.mp4"


def clip_state(video: Path, clip: Path, probe: bool = True) -> dict:
    clip = clip.resolve()
    project_id = path_id(video)
    highlight = layout.highlight_dir_for_clip(clip)
    captions_json = layout.sidecar(clip, "captions.json")
    framing_json = layout.sidecar(clip, "framing.json")
    public_video = APP_PUBLIC_DIR / layout.public_name(clip)
    rendered = rendered_path(video, clip)
    in_public = public_video.is_file()

    # a render goes stale when any of its inputs (clip, captions, framing, caption
    # style — either copy) changed after it was produced; caption edits must visibly
    # propagate to the render step instead of silently showing an old file
    rendered_mtime = rendered.stat().st_mtime if rendered.is_file() else None
    sidecars = ("captions.json", "framing.json", "style.json", "cuts.json")
    sources = [clip, public_video]
    sources += [layout.sidecar(clip, suffix) for suffix in sidecars]
    sources += [APP_PUBLIC_DIR / layout.public_sidecar_name(clip, suffix) for suffix in sidecars]
    source_mtime = max((p.stat().st_mtime for p in sources if p.is_file()), default=None)
    stale = bool(rendered_mtime and source_mtime and source_mtime > rendered_mtime + 1)

    state = {
        "id": path_id(clip),
        "path": str(clip),
        "name": clip.name,
        "exists": clip.is_file(),
        # which highlight this clip belongs to, and the stage folder it came out of
        "highlight": highlight.name if highlight is not None else None,
        "folder": str(highlight if highlight is not None else clip.parent),
        "stage": clip.parent.name if clip.parent.name in layout.CLIP_DIRS else None,
        "has_captions": captions_json.is_file(),
        "has_framing": framing_json.is_file(),
        "has_corrections": layout.sidecar(clip, "corrections.json").is_file(),
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
            "video": f"/media/app/{layout.public_name(clip)}" if in_public else None,
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
            **_playable_state(video, probe),
            **(_probe_safe(video) if (probe and video.is_file()) else {}),
        },
        "artifacts": {name: _artifact(path) for name, path in arts.items()},
        "source_dir": str(layout.source_dir(video)),
        # None = every finished video lands in its own highlight folder
        "output_dir": str(configured) if (configured := output_dir(video)) else None,
        # everyone the programme has preaching in this folder: the first one pre-fills
        # each clip's intro, the rest are offered beside the field
        "speakers": load_speakers(video.parent),
        "clips": clips,
        "steps": steps,
    }
    return state
