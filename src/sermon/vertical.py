"""Export a highlight as a vertical, speaker-tracked clip for editing in DaVinci.

The vertical-first flow: instead of tracking after the DaVinci edit, each
highlight is tracked and cropped to 9:16 *before* editing, so B-roll is placed
in the final vertical frame. The export is a single ffmpeg pass — the solved
camera path drives a dynamic crop via sendcmd, scaled to 1080x1920 and encoded
with hardware ProRes (HQ): visually lossless intermediate quality, since the
clip is re-encoded twice more (DaVinci render, then the caption burn-in).
Speed and quality are the priorities here, not file size.
"""

import subprocess
from functools import cache
from pathlib import Path
from typing import Callable

from .track import OUT_ASPECT, compute_camera_path, probe_video


@cache
def _has_prores_videotoolbox() -> bool:
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, check=False
    ).stdout
    return "prores_videotoolbox" in out


def export_vertical_clip(
    video: Path,
    start_sec: float,
    end_sec: float,
    out_path: Path,
    on_progress: Callable[[float], None] | None = None,
) -> Path:
    """Track the speaker in [start_sec, end_sec] of `video` and render that
    window as a 1080x1920 clip with the crop following the speaker."""
    video = video.resolve()
    out_path = out_path.resolve()
    meta = probe_video(video)
    duration = max(0.5, min(end_sec, meta["duration"]) - start_sec)

    t_grid, camera, _cuts, _crop_w, _samples = compute_camera_path(
        video, meta, start=start_sec, duration=duration
    )

    width, height = meta["width"], meta["height"]
    crop_px = int(round(OUT_ASPECT * height / 2)) * 2
    max_x = width - crop_px

    def crop_x(cx: float) -> int:
        # crop-left edge in px, clamped and even (chroma subsampling)
        x = int(round((cx * width - crop_px / 2) / 2)) * 2
        return min(max(x, 0), max_x)

    # sendcmd drives the crop's x per solved sample (25 Hz), same mechanism as
    # the tracking debug overlay; pts are relative to start (-ss precedes -i)
    lines = [f"{t:.3f} crop@cam x {crop_x(cx)};" for t, cx in zip(t_grid, camera)]
    cmd_file = out_path.parent / f".{out_path.stem}.sendcmd"
    cmd_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    if _has_prores_videotoolbox():
        codec = ["-c:v", "prores_videotoolbox", "-profile:v", "hq"]
    else:  # still hardware, still generous quality headroom
        codec = ["-c:v", "hevc_videotoolbox", "-b:v", "60M", "-tag:v", "hvc1"]

    argv = [
        "ffmpeg", "-v", "error", "-y",
        "-ss", f"{start_sec:.3f}", "-i", str(video), "-t", f"{duration:.3f}",
        "-vf",
        f"sendcmd=f='{cmd_file.name}',"
        f"crop@cam=w={crop_px}:h=ih:x={crop_x(float(camera[0]))}:y=0,"
        f"scale=1080:1920:flags=lanczos",
        *codec,
        "-c:a", "pcm_s16le",
        "-progress", "pipe:1", "-nostats",
        str(out_path),
    ]
    proc = subprocess.Popen(
        argv, cwd=out_path.parent, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        if line.startswith("out_time_ms=") and on_progress:
            try:
                on_progress(min(int(line.split("=", 1)[1]) / 1e6 / duration * 100, 100.0))
            except ValueError:
                pass
    proc.wait()
    cmd_file.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr.read() if proc.stderr else ''}".strip())
    return out_path
