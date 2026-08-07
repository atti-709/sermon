"""Export a highlight as a vertical, speaker-tracked clip for editing in DaVinci.

The vertical-first flow: instead of tracking after the DaVinci edit, each
highlight is tracked and cropped to 9:16 *before* editing, so B-roll is placed
in the final vertical frame. The export is a single ffmpeg pass — a static crop
takes the widest window the pan ever needs, then a `perspective` filter slides
the exact fractional crop quad across it with per-frame expressions
(eval=frame, cubic sampling), so the pan is evaluated at the full frame rate
with *subpixel* positions. An integer crop driven by sendcmd was visibly
steppy: 25 Hz updates on a 50 fps source moved the window every other frame,
and even-pixel rounding turned slow pan tails into 3-4 output-px stutter.
The result is scaled to 1080x1920 and encoded with hardware h264 at 30 Mbps.
Benchmarked against a lossless reference of the filtered source, that setting
scored *above* ProRes HQ on SSIM (0.9975 vs 0.9966) at 1/11 the size — the
crop upscales a slice of an already-compressed source, so intra-only mezzanine
codecs carry no extra information, only bytes. It also stays decodable
everywhere downstream (Resolve, Chrome preview, WebCodecs render) with no
transcode gate needed.

With a hook range the export opens with it: the hook moment is cut in front of
the full passage (the same structure the Resolve XML lays out), so the clip
stops the scroll in its first seconds even when it never goes through DaVinci.
Both pieces are tracked separately and joined inside one filtergraph, so there
is still a single encode and the seam carries continuous timestamps.
"""

import json
import subprocess
from pathlib import Path
from typing import Callable

import numpy as np

from . import layout
from .track import OUT_ASPECT, compute_camera_path, probe_video

MIN_SEGMENT_SEC = 0.5
PATH_EPSILON_PX = 0.15  # tolerance when thinning the per-frame path into expression nodes


def _thin_path(xs: np.ndarray, eps: float) -> list[int]:
    """Douglas-Peucker over (index, value); returns the kept indices."""
    keep = np.zeros(len(xs), dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(xs) - 1)]
    while stack:
        i0, i1 = stack.pop()
        if i1 <= i0 + 1:
            continue
        t = np.arange(i0 + 1, i1)
        interp = xs[i0] + (xs[i1] - xs[i0]) * (t - i0) / (i1 - i0)
        dev = np.abs(xs[i0 + 1:i1] - interp)
        worst = int(np.argmax(dev))
        if dev[worst] > eps:
            mid = i0 + 1 + worst
            keep[mid] = True
            stack.extend([(i0, mid), (mid, i1)])
    return list(np.where(keep)[0])


def _sum_tree(terms: list[str], leaf: int = 32) -> str:
    """Sum the terms as a balanced parenthesized tree: ffmpeg's expression parser
    recurses once per chained `+`, and a flat chain of >~100 terms overflows its
    recursion guard (surfacing as a bogus \"Cannot allocate memory\")."""
    if len(terms) <= leaf:
        return "+".join(terms)
    mid = len(terms) // 2
    return f"({_sum_tree(terms[:mid], leaf)})+({_sum_tree(terms[mid:], leaf)})"


def _pan_expr(nodes: list[tuple[int, float]]) -> str:
    """The crop-left edge (px, fractional) as an ffmpeg expression of the frame
    index `in`: piecewise-linear between the thinned path nodes."""
    if len(nodes) < 2 or all(abs(x - nodes[0][1]) < 1e-6 for _, x in nodes):
        return f"{nodes[0][1]:.3f}"
    terms = []
    for (n0, x0), (n1, x1) in zip(nodes, nodes[1:]):
        slope = (x1 - x0) / (n1 - n0)
        body = f"{x0:.3f}" if abs(slope) < 1e-7 else f"{x0:.3f}+(in-{n0})*{slope:.7f}"
        terms.append(f"gte(in,{n0})*lt(in,{n1})*({body})")
    terms.append(f"gte(in,{nodes[-1][0]})*{nodes[-1][1]:.3f}")
    return _sum_tree(terms)


def _has_audio(video: Path) -> bool:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(video)],
        capture_output=True, text=True, check=False,
    ).stdout
    return bool(out.strip())


def export_vertical_clip(
    video: Path,
    start_sec: float,
    end_sec: float,
    out_path: Path,
    on_progress: Callable[[float], None] | None = None,
    hook: tuple[float, float] | None = None,
    subject_x: float | None = None,
    follow_speaker: bool = False,
) -> Path:
    """Track the speaker in [start_sec, end_sec] of `video` and render that
    window as a 1080x1920 clip with the crop following the speaker.

    `hook` is an optional (start, end) window of the same video to play first —
    usually a moment from inside the passage, which then repeats in context.

    `subject_x` picks which person to follow when the frame holds several (see
    ../track.py). Every segment gets the same one: they are tracked separately, so
    without it the hook and the passage can each lock onto a different panelist.

    `follow_speaker` instead gives the frame to whoever is talking and cuts between
    them (see ../speakers.py). Each segment is analyzed on its own, which is right
    here: the hook and the passage are different stretches of the conversation."""
    video = video.resolve()
    out_path = layout.ensure_parent(out_path.resolve())  # the filtergraph script lands here too
    meta = probe_video(video)

    segments = []
    if hook is not None and hook[1] - hook[0] >= MIN_SEGMENT_SEC:
        segments.append(hook)
    segments.append((start_sec, end_sec))
    # (start, duration) pairs, clamped to the source
    cuts = [(s, max(MIN_SEGMENT_SEC, min(e, meta["duration"]) - s)) for s, e in segments]
    total = sum(duration for _, duration in cuts)

    width, height = meta["width"], meta["height"]
    fps = meta["fps"]
    crop_w = OUT_ASPECT * height  # fractional px — the perspective quad is subpixel

    inputs: list[str] = []
    chains: list[str] = []
    for i, (start, duration) in enumerate(cuts):
        if len(cuts) > 1:
            print(f"  segment {i + 1}/{len(cuts)}: {start:.1f}s + {duration:.1f}s")
        t_grid, camera, _cuts, _crop_frac, _samples = compute_camera_path(
            video, meta, start=start, duration=duration, subject_x=subject_x,
            follow_speaker=follow_speaker,
        )
        # the solved path, sampled at every output frame (frame counts restart per
        # input: -ss precedes -i), as the crop's fractional left edge in source px
        n_frames = max(2, int(round(duration * fps)) + 1)
        cam = np.interp(np.arange(n_frames) / fps, t_grid, camera)
        left = np.clip(cam * width - crop_w / 2, 0.0, width - crop_w)

        # a static crop takes the widest window this segment's pan ever needs
        # (even bounds for chroma subsampling, padded for the cubic kernel);
        # the perspective expressions then pan inside it with subpixel positions
        win_x0 = max(0, (int(np.floor(left.min())) - 6) // 2 * 2)
        win_x1 = min(width, ((int(np.ceil(left.max() + crop_w)) + 7) // 2) * 2)
        win_w = win_x1 - win_x0
        nodes = [(int(k), float(left[k] - win_x0)) for k in _thin_path(left, PATH_EPSILON_PX)]
        expr = _pan_expr(nodes)
        inputs += ["-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(video)]
        chains.append(
            f"[{i}:v]crop=w={win_w}:h=ih:x={win_x0}:y=0,"
            f"perspective=x0='{expr}':y0=0:x1='({expr})+{crop_w:.2f}':y1=0:"
            f"x2='{expr}':y2=H:x3='({expr})+{crop_w:.2f}':y3=H:"
            f"sense=source:eval=frame:interpolation=cubic,"
            f"scale=1080:1920:flags=lanczos,setsar=1[v{i}]"
        )

    audio = _has_audio(video)
    if len(cuts) > 1:
        streams = "".join(f"[v{i}][{i}:a]" if audio else f"[v{i}]" for i in range(len(cuts)))
        chains.append(f"{streams}concat=n={len(cuts)}:v=1:a={int(audio)}[v]" + ("[a]" if audio else ""))
        maps = ["-map", "[v]"] + (["-map", "[a]"] if audio else [])
    else:
        maps = ["-map", "[v0]"] + (["-map", "0:a"] if audio else [])

    # the pan expressions run to tens of KB — pass the graph as a script file
    script_file = out_path.parent / f".{out_path.stem}.filtergraph"
    script_file.write_text(";\n".join(chains) + "\n", encoding="utf-8")

    argv = [
        "ffmpeg", "-v", "error", "-y",
        *inputs,
        "-filter_complex_script", str(script_file),
        *maps,
        "-c:v", "h264_videotoolbox", "-b:v", "30M",
        "-c:a", "pcm_s16le",
        "-movflags", "+faststart",
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
                on_progress(min(int(line.split("=", 1)[1]) / 1e6 / total * 100, 100.0))
            except ValueError:
                pass
    proc.wait()
    script_file.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr.read() if proc.stderr else ''}".strip())
    write_cut_sidecar(out_path, [d for _, d in cuts])
    return out_path


def cuts_file_for(clip: Path) -> Path:
    return layout.sidecar(clip, "cuts.json")


def write_cut_sidecar(out_path: Path, durations: list[float]) -> None:
    """Record where the exported clip is spliced, in its own timeline.

    Captioning has to know: WhisperX hears the splice as one continuous
    utterance and drags words across it, so it transcribes each piece on its
    own. A single-piece export clears any sidecar from an earlier run."""
    path = cuts_file_for(out_path)
    seams = [round(sum(durations[: i + 1]), 3) for i in range(len(durations) - 1)]
    if not seams:
        path.unlink(missing_ok=True)
        return
    layout.ensure_parent(path).write_text(json.dumps({"cuts": seams}, indent=2) + "\n", encoding="utf-8")
