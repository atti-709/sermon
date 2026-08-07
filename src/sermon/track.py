"""Speaker tracking for the 9:16 reframe: Apple Vision face detection + a virtual-camera path.

The Remotion app crops the 16:9 clip to 9:16 (`objectFit: cover`), which by default shows the
center. This module finds where the speaker actually is and writes a `framing.json` sidecar
(in the clip's `03_CAPTIONING/` folder) with per-time X positions for the crop window,
styled after a human camera operator: the camera
HOLDS still while the subject stays inside a dead zone and only PANS (minimum-jerk, with
look-ahead) once they have clearly moved — less motion beats more motion.

Detection runs on the Apple Neural Engine via the Vision framework (pyobjc), a few ms per
frame. Scene cuts are detected with ffmpeg and only honored when the subject position actually
jumps across them (LED-wall slide changes behind the speaker must not snap the camera).
"""

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import layout
from .captions import APP_PUBLIC_DIR

# ---------------------------------------------------------------------------
# Tunables (units: fraction of source width, seconds)
SAMPLE_HZ = 10.0  # detection rate; the camera path is solved at SOLVE_HZ regardless
SOLVE_HZ = 50.0  # fine enough that per-frame sampling of the path stays kink-free
DETECT_WIDTH = 960  # frames are decoded at this width for detection
OUT_ASPECT = 9 / 16  # the vertical crop
DEAD_ZONE = 0.058  # subject may drift this far from the crop center before a pan (~110 px @1920)
CONFIRM_SEC = 0.9  # subject must stay outside the dead zone this long to trigger a pan
CONFIRM_FRAC = 0.85  # ...for at least this fraction of the confirm window
REACTION_SEC = 0.35  # ...and must have already been drifting this long before the pan
# launches — a human operator reacts to movement they have seen, they don't anticipate it
REACTION_EMERGENCY_SEC = 0.15  # sudden big moves get the startle-reflex reaction instead
EXCURSION_WIN_SEC = 3.5  # brief excursions that return within this window are not followed...
EXCURSION_MIN_FRAC = 0.45  # ...unless the subject spends this fraction of it outside the zone
EMERGENCY_SPEED = 0.10  # but follow right away when the subject is outside the dead zone and
# observed moving away faster than this (width/s) — inferred from the past, not the future
MIN_HOLD_SEC = 0.6  # a completed pan is followed by at least this much stillness
PAN_SEC_BASE, PAN_SEC_PER_DIST = 0.5, 4.5  # pan duration = base + dist * per_dist
PAN_SEC_MIN, PAN_SEC_MAX = 1.1, 2.8
REPLAN_EVERY_SEC = 0.4  # while panning, re-aim at the subject's updated position
BRAKE_SEC = 0.5  # a pan never reverses direction: it brakes, holds, then pans anew
MEDIAN_WIN = 5  # samples; kills single-sample detector jumps
SCENE_THRESHOLD = 0.20  # ffmpeg scene score candidate threshold (candidates are cheap:
# they only take effect when the subject position actually jumps across them)
CUT_MIN_JUMP = 0.075  # subject must jump this far across a candidate cut to accept it
FACE_MIN_CONFIDENCE = 0.3
KEYFRAME_EPSILON = 0.0008  # Douglas-Peucker tolerance on the emitted path
# ---------------------------------------------------------------------------


@dataclass
class Sample:
    t: float
    cx: float | None  # normalized [0..1] subject center, None = nothing detected
    cy: float | None
    kind: str  # "face" | "human" | "none"
    confidence: float


def probe_video(video: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,r_frame_rate,duration:format=duration", "-of", "json", str(video)],
        check=True, capture_output=True, text=True,
    ).stdout
    info = json.loads(out)
    stream = info["streams"][0]
    num, den = stream["r_frame_rate"].split("/")
    # Matroska keeps no per-stream duration — only MP4/MOV-family containers store
    # one per track. The container's own duration is the fallback, and for a
    # single-video-stream sermon the two are the same number anyway.
    duration = stream.get("duration") or info.get("format", {}).get("duration")
    return {
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "fps": float(num) / float(den),
        "duration": float(duration),
    }


# ---------------------------------------------------------------------------
# Detection


def _seek_args(start: float | None) -> list[str]:
    return ["-ss", f"{start:.3f}"] if start else []


def _dur_args(duration: float | None) -> list[str]:
    return ["-t", f"{duration:.3f}"] if duration else []


def _scene_cut_candidates(video: Path, start: float | None = None,
                          duration: float | None = None) -> list[float]:
    """Timestamps whose frame differs strongly from the previous one (possible hard cuts).

    With `start`/`duration` only that window is analyzed; timestamps come back
    relative to `start` (ffmpeg resets pts when -ss precedes -i)."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", *_seek_args(start), "-i", str(video), *_dur_args(duration),
         "-vf", f"scale=320:-2,select='gt(scene,{SCENE_THRESHOLD})',metadata=print:file=-",
         "-f", "null", "-"],
        check=True, capture_output=True, text=True,
    )
    cuts = []
    for line in proc.stdout.splitlines():
        if "pts_time:" in line:
            cuts.append(float(line.rsplit("pts_time:", 1)[1]))
    return cuts


def _detect_samples(video: Path, meta: dict, sample_hz: float, start: float | None = None,
                    duration: float | None = None) -> list[Sample]:
    """Decode frames at `sample_hz` and find the main subject with the Vision framework."""
    import Quartz
    import Vision

    src_ar = meta["width"] / meta["height"]
    w = DETECT_WIDTH
    h = int(round(w / src_ar / 2) * 2)
    frame_bytes = w * h * 3

    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", *_seek_args(start), "-i", str(video), *_dur_args(duration),
         "-vf", f"fps={sample_hz},scale={w}:{h}",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE,
    )

    color_space = Quartz.CGColorSpaceCreateDeviceRGB()
    samples: list[Sample] = []
    last: tuple[float, float] | None = None  # (t, cx) of the last confident subject fix

    index = 0
    assert proc.stdout is not None
    while True:
        buf = proc.stdout.read(frame_bytes)
        if len(buf) < frame_bytes:
            break
        t = index / sample_hz
        index += 1

        provider = Quartz.CGDataProviderCreateWithData(None, buf, frame_bytes, None)
        image = Quartz.CGImageCreate(
            w, h, 8, 24, w * 3, color_space, Quartz.kCGImageAlphaNone,
            provider, None, False, Quartz.kCGRenderingIntentDefault,
        )
        handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(image, None)
        face_req = Vision.VNDetectFaceRectanglesRequest.alloc().init()
        face_req.setRevision_(Vision.VNDetectFaceRectanglesRequestRevision3)
        handler.performRequests_error_([face_req], None)

        candidates = []  # (score, cx, cy, conf, kind)
        for r in face_req.results() or []:
            if r.confidence() < FACE_MIN_CONFIDENCE:
                continue
            bb = r.boundingBox()  # normalized, origin bottom-left
            cx = bb.origin.x + bb.size.width / 2
            cy = 1.0 - (bb.origin.y + bb.size.height / 2)
            score = r.confidence() * np.sqrt(bb.size.height)
            if last is not None:
                # favor the face nearest the running track; the gate widens while blind
                gate = min(0.08 + 0.4 * (t - last[0]), 0.5)
                dist = abs(cx - last[1])
                if dist > gate and len(face_req.results()) > 1:
                    continue
                score -= 1.5 * dist
            candidates.append((score, cx, cy, float(r.confidence()), "face"))

        if not candidates:
            # face lost (turned away, occluded): fall back to the human-body detector
            human_req = Vision.VNDetectHumanRectanglesRequest.alloc().init()
            handler2 = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(image, None)
            handler2.performRequests_error_([human_req], None)
            for r in human_req.results() or []:
                if r.confidence() < FACE_MIN_CONFIDENCE:
                    continue
                bb = r.boundingBox()
                cx = bb.origin.x + bb.size.width / 2
                cy = 1.0 - (bb.origin.y + bb.size.height / 2)
                score = 0.5 * float(r.confidence()) * np.sqrt(bb.size.width)
                if last is not None:
                    gate = min(0.08 + 0.4 * (t - last[0]), 0.5)
                    if abs(cx - last[1]) > gate and len(human_req.results()) > 1:
                        continue
                    score -= 1.5 * abs(cx - last[1])
                candidates.append((score, cx, cy, float(r.confidence()), "human"))

        if candidates:
            _, cx, cy, conf, kind = max(candidates, key=lambda c: c[0])
            samples.append(Sample(t, float(cx), float(cy), kind, conf))
            last = (t, float(cx))
        else:
            samples.append(Sample(t, None, None, "none", 0.0))

    proc.wait()
    return samples


# ---------------------------------------------------------------------------
# Track cleanup and cut confirmation


def _clean_track(samples: list[Sample], duration: float) -> tuple[np.ndarray, np.ndarray]:
    """Return (t_grid, subject_x) at SOLVE_HZ: outlier-filtered and gap-interpolated."""
    obs_t = np.array([s.t for s in samples if s.cx is not None])
    obs_x = np.array([s.cx for s in samples if s.cx is not None])
    t_grid = np.arange(0.0, duration, 1.0 / SOLVE_HZ)
    if len(obs_t) == 0:
        return t_grid, np.full_like(t_grid, 0.5)

    # rolling median over the observed track kills single-sample detector jumps
    if len(obs_x) >= MEDIAN_WIN:
        pad = MEDIAN_WIN // 2
        padded = np.concatenate([obs_x[:pad][::-1], obs_x, obs_x[-pad:][::-1]])
        obs_x = np.array(
            [np.median(padded[i:i + MEDIAN_WIN]) for i in range(len(obs_x))]
        )

    # linear interpolation across gaps; ends are held flat
    return t_grid, np.interp(t_grid, obs_t, obs_x)


def _confirm_cuts(candidates: list[float], t_grid: np.ndarray, subject: np.ndarray) -> list[float]:
    """Keep only candidate cuts across which the subject position actually jumps.

    The LED wall behind the speaker changes slides, which produces high scene scores
    without any real shot change — snapping the camera there would look broken."""
    cuts = []
    for c in candidates:
        before = subject[(t_grid >= c - 0.5) & (t_grid <= c - 0.04)]
        after = subject[(t_grid >= c + 0.04) & (t_grid <= c + 0.5)]
        if len(before) == 0 or len(after) == 0:
            continue
        if abs(float(np.median(after)) - float(np.median(before))) >= CUT_MIN_JUMP:
            if not cuts or c - cuts[-1] > 0.25:
                cuts.append(c)
    return cuts


# ---------------------------------------------------------------------------
# Virtual camera


def _quintic(x0: float, v0: float, a0: float, x1: float, T: float) -> np.ndarray:
    """Minimum-jerk polynomial coefficients from (x0, v0, a0) to (x1, v=0, a=0) in T seconds."""
    #   x(t) = x0 + v0 t + a0/2 t^2 + c3 t^3 + c4 t^4 + c5 t^5
    #   constraints: x(T)=x1, x'(T)=0, x''(T)=0  ->  linear system in (c3, c4, c5)
    d = x1 - x0 - v0 * T - 0.5 * a0 * T * T
    T2, T3, T4, T5 = T * T, T ** 3, T ** 4, T ** 5
    A = np.array([
        [T3, T4, T5],
        [3 * T2, 4 * T3, 5 * T4],
        [6 * T, 12 * T2, 20 * T3],
    ])
    b = np.array([d, -v0 - a0 * T, -a0])
    c3, c4, c5 = np.linalg.solve(A, b)
    return np.array([x0, v0, a0 / 2, c3, c4, c5])


def _poly_eval(c: np.ndarray, t: float) -> tuple[float, float, float]:
    x = c[0] + c[1] * t + c[2] * t**2 + c[3] * t**3 + c[4] * t**4 + c[5] * t**5
    v = c[1] + 2 * c[2] * t + 3 * c[3] * t**2 + 4 * c[4] * t**3 + 5 * c[5] * t**4
    a = 2 * c[2] + 6 * c[3] * t + 12 * c[4] * t**2 + 20 * c[5] * t**3
    return float(x), float(v), float(a)


def _solve_camera(t_grid: np.ndarray, subject: np.ndarray, cuts: list[float],
                  crop_half: float) -> np.ndarray:
    """The virtual operator: hold while the subject is inside the dead zone, pan smoothly
    (with look-ahead, re-aiming mid-pan) once they have clearly left it."""
    lo, hi = crop_half, 1.0 - crop_half
    clamp = lambda x: float(np.clip(x, lo, hi))
    dt = 1.0 / SOLVE_HZ
    pan_sec = lambda dist: float(np.clip(PAN_SEC_BASE + PAN_SEC_PER_DIST * dist, PAN_SEC_MIN, PAN_SEC_MAX))
    confirm_n = max(1, int(round(CONFIRM_SEC * SOLVE_HZ)))
    excursion_n = max(1, int(round(EXCURSION_WIN_SEC * SOLVE_HZ)))
    react_n = max(1, int(round(REACTION_SEC * SOLVE_HZ)))
    react_fast_n = max(1, int(round(REACTION_EMERGENCY_SEC * SOLVE_HZ)))
    camera = np.empty_like(subject)

    bounds = [0.0, *cuts, t_grid[-1] + dt]
    for b0, b1 in zip(bounds, bounds[1:]):
        seg = np.where((t_grid >= b0) & (t_grid < b1))[0]
        if len(seg) == 0:
            continue
        s = subject[seg]
        n = len(seg)

        x = clamp(float(np.median(s[: min(n, int(1.0 * SOLVE_HZ))])))
        v = a = 0.0
        panning = False
        poly, tau, pan_T, target = None, 0.0, 0.0, x
        since_replan, hold_for = 0.0, 0.0

        def pan_target(k: int) -> float:
            """Aim where the subject will settle once a pan launched at step k lands."""
            rough_T = pan_sec(abs(s[min(k + confirm_n, n - 1)] - x))
            arrive = min(k + int(rough_T * SOLVE_HZ), n - 1)
            look = s[max(0, arrive - int(0.2 * SOLVE_HZ)): min(n, arrive + int(1.2 * SOLVE_HZ) + 1)]
            return clamp(float(np.median(look)))

        for k in range(n):
            if not panning:
                hold_for += dt
                win = s[k: min(k + confirm_n, n)]
                frac_out = float(np.mean(np.abs(win - x) > DEAD_ZONE))
                # brief excursions that come right back are ignored; the long window decides
                frac_out_long = float(np.mean(np.abs(s[k: min(k + excursion_n, n)] - x) > DEAD_ZONE))
                settled = frac_out >= CONFIRM_FRAC and frac_out_long >= EXCURSION_MIN_FRAC
                # a sharp operator reads speed off the subject: outside the zone and
                # visibly striding away -> chase now, don't wait out the confirm window
                v_obs = (s[k] - s[k - react_fast_n]) / REACTION_EMERGENCY_SEC if k >= react_fast_n else 0.0
                emergency = abs(s[k] - x) > DEAD_ZONE and v_obs * np.sign(s[k] - x) > EMERGENCY_SPEED
                # the drift must have been visible for a moment already: pans react, never anticipate
                def seen_for(steps: int) -> bool:
                    recent = s[max(0, k - steps): k + 1]
                    return k >= steps and float(np.mean(np.abs(recent - x) > 0.6 * DEAD_ZONE)) >= 0.7

                ready = (settled and seen_for(react_n)) or (emergency and seen_for(react_fast_n))
                if hold_for >= MIN_HOLD_SEC and abs(s[k] - x) > 0.6 * DEAD_ZONE and ready:
                    target = pan_target(k)
                    if abs(target - x) > 0.4 * DEAD_ZONE:
                        # catch-up whips are brisker: the subject is escaping the frame
                        pan_T = pan_sec(abs(target - x)) * (0.72 if emergency else 1.0)
                        poly = _quintic(x, v, a, target, pan_T)
                        tau, since_replan, panning = 0.0, 0.0, True
            else:
                tau += dt
                since_replan += dt
                if since_replan >= REPLAN_EVERY_SEC and pan_T - tau > 0.35:
                    fresh = pan_target(k)
                    if abs(fresh - target) > 0.75 * DEAD_ZONE:
                        # distance to the fresh target measured along the direction of travel
                        ahead = (fresh - x) * np.sign(v) if v else abs(fresh - x)
                        if abs(v) > 0.03 and ahead < abs(v) * 0.3:
                            # target now behind us (or too close to keep going): never whip
                            # back mid-pan — brake to a stop, hold, then pan anew
                            stop = clamp(x + v * BRAKE_SEC * 0.35)
                            poly, target, pan_T, tau = _quintic(x, v, a, stop, BRAKE_SEC), stop, BRAKE_SEC, dt
                        else:
                            remaining = max(pan_T - tau, 0.8 * pan_sec(abs(fresh - x)))
                            poly, target, pan_T, tau = _quintic(x, v, a, fresh, remaining), fresh, remaining, dt
                    since_replan = 0.0
                if tau >= pan_T:
                    x, v, a, panning, hold_for = target, 0.0, 0.0, False, 0.0
                else:
                    x, v, a = _poly_eval(poly, tau)
                    x = clamp(x)
            camera[seg[k]] = x

    return camera


def _thin_keyframes(t_grid: np.ndarray, camera: np.ndarray, cuts: list[float]) -> list[dict]:
    """Douglas-Peucker per segment; a cut becomes two keyframes 1 ms apart (a step)."""

    def douglas_peucker(ts: np.ndarray, xs: np.ndarray) -> list[int]:
        keep = np.zeros(len(ts), dtype=bool)
        keep[0] = keep[-1] = True
        stack = [(0, len(ts) - 1)]
        while stack:
            i0, i1 = stack.pop()
            if i1 <= i0 + 1:
                continue
            span = ts[i1] - ts[i0] or 1.0
            interp = xs[i0] + (xs[i1] - xs[i0]) * (ts[i0 + 1:i1] - ts[i0]) / span
            dev = np.abs(xs[i0 + 1:i1] - interp)
            worst = int(np.argmax(dev))
            if dev[worst] > KEYFRAME_EPSILON:
                mid = i0 + 1 + worst
                keep[mid] = True
                stack.extend([(i0, mid), (mid, i1)])
        return list(np.where(keep)[0])

    keyframes: list[dict] = []
    bounds = [0.0, *cuts, float(t_grid[-1]) + 1.0]
    for b0, b1 in zip(bounds, bounds[1:]):
        seg = np.where((t_grid >= b0) & (t_grid < b1))[0]
        if len(seg) == 0:
            continue
        ts, xs = t_grid[seg], camera[seg]
        for i in douglas_peucker(ts, xs):
            keyframes.append({"t": round(float(ts[i]), 3), "cx": round(float(xs[i]), 5)})
    return keyframes


# ---------------------------------------------------------------------------
# Entry point


def framing_file_for(video: Path) -> Path:
    return layout.sidecar(video, "framing.json")


def compute_camera_path(video: Path, meta: dict, sample_hz: float = SAMPLE_HZ,
                        start: float | None = None, duration: float | None = None,
                        ) -> tuple[np.ndarray, np.ndarray, list[float], float, list[Sample]]:
    """Detect the subject and solve the virtual camera for the whole video or a
    time window. Returns (t_grid, camera, cuts, crop_width_fraction, samples);
    times are relative to `start`."""
    span = duration if duration is not None else meta["duration"]

    cut_candidates = _scene_cut_candidates(video, start, duration)
    samples = _detect_samples(video, meta, sample_hz, start, duration)
    detected = sum(1 for s in samples if s.kind != "none")
    faces = sum(1 for s in samples if s.kind == "face")
    print(f"  detections: {detected}/{len(samples)} samples ({faces} face, {detected - faces} body)")

    t_grid, subject = _clean_track(samples, span)
    cuts = _confirm_cuts(cut_candidates, t_grid, subject)
    if cut_candidates:
        detail = ", ".join(f"{c:.2f}s{'✓' if c in cuts else '✗'}" for c in cut_candidates)
        print(f"  cuts: {len(cuts)} confirmed of {len(cut_candidates)} scene-change candidates ({detail})")

    # crop width as a fraction of source width (e.g. 0.316 for 16:9 -> 9:16)
    crop_w = OUT_ASPECT * meta["height"] / meta["width"]
    camera = _solve_camera(t_grid, subject, cuts, crop_half=crop_w / 2)

    vel = np.abs(np.diff(camera)) * SOLVE_HZ
    moving = float(np.mean(vel > 0.002))
    print(f"  camera: still {100 * (1 - moving):.0f}% of the time, peak speed {vel.max():.3f} width/s")

    return t_grid, camera, cuts, crop_w, samples


def track_video(video: Path, sample_hz: float = SAMPLE_HZ, copy_to_app: bool = True,
                debug: bool = False) -> dict[str, Path]:
    meta = probe_video(video)
    duration = meta["duration"]

    t_grid, camera, cuts, crop_w, samples = compute_camera_path(video, meta, sample_hz)
    keyframes = _thin_keyframes(t_grid, camera, cuts)
    print(f"  emitted {len(keyframes)} keyframes")

    framing = {
        "version": 1,
        "video": video.name,
        "sourceWidth": meta["width"],
        "sourceHeight": meta["height"],
        "duration": round(duration, 3),
        "cropWidthFraction": round(crop_w, 6),
        "cuts": [round(c, 3) for c in cuts],
        "keyframes": keyframes,
    }

    json_path = layout.ensure_parent(framing_file_for(video))
    json_path.write_text(json.dumps(framing, indent=2) + "\n", encoding="utf-8")
    paths = {"framing": json_path}

    if copy_to_app and APP_PUBLIC_DIR.is_dir():
        app_path = APP_PUBLIC_DIR / layout.public_sidecar_name(video, "framing.json")
        if app_path.resolve() != json_path.resolve():
            shutil.copy2(json_path, app_path)
        paths["app"] = app_path

    if debug:
        paths["debug"] = _write_debug_overlay(video, samples, t_grid, camera, crop_w, meta)

    return paths


def _write_debug_overlay(video: Path, samples: list[Sample], t_grid: np.ndarray,
                         camera: np.ndarray, crop_w: float, meta: dict) -> Path:
    """Render a preview with the crop window (yellow) and detections (red) burned in."""
    w = meta["width"]
    crop_px = int(crop_w * w)
    cmd_lines = []
    for t, cx in zip(t_grid, camera):
        cmd_lines.append(f"{t:.3f} drawbox@cam x {int((cx - crop_w / 2) * w)};")
    for s in samples:
        x = int(s.cx * w) - 3 if s.cx is not None else -10
        cmd_lines.append(f"{s.t:.3f} drawbox@det x {x};")
    cmd_file = video.resolve().parent / f"{video.stem}.framing-debug.cmd"
    cmd_file.write_text("\n".join(sorted(cmd_lines, key=lambda l: float(l.split()[0]))) + "\n")

    # a diagnostic, not a deliverable — it belongs with the tracking metadata
    out = layout.ensure_parent(layout.sidecar(video, "framing-debug.mp4"))
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(video.resolve()),
         "-vf",
         f"sendcmd=f='{cmd_file.name}',"
         f"drawbox@cam=x=0:y=0:w={crop_px}:h=ih:color=yellow@0.9:thickness=5,"
         f"drawbox@det=x=-10:y=0:w=6:h=ih:color=red@0.7:thickness=fill,"
         f"scale=960:-2",
         "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", str(out)],
        check=True, cwd=video.resolve().parent, capture_output=True, text=True,
    )
    cmd_file.unlink(missing_ok=True)
    return out
