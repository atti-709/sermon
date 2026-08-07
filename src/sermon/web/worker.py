"""Subprocess entry point for pipeline jobs: `python -m sermon.web.worker <kind> …`.

Emits a JSONL protocol on stdout — one object per line, with an "event" key of
progress | log | result | error. Library noise (tqdm, HF downloads) stays on
stderr and is streamed to the UI as raw log lines by the job manager.

Runs as a separate process so that whisperx's leaked non-daemon threads and
mlx's GPU state never touch the server (the captions run ends with os._exit,
mirroring the CLI).
"""

import argparse
import contextlib
import io
import json
import os
import re
import sys
from pathlib import Path

_REAL_STDOUT = sys.stdout

# mlx-whisper verbose segment lines: "[MM:SS.mmm --> MM:SS.mmm] text" (hours only when >= 1h)
SEGMENT_END_RE = re.compile(r"-->\s*(?:(\d+):)?(\d{1,2}):(\d{2})\.(\d{3})\]")


def emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False), file=_REAL_STDOUT, flush=True)


class _LineWriter(io.TextIOBase):
    """Redirect target that turns library print() output into protocol events."""

    def __init__(self, handle_line) -> None:
        self._handle_line = handle_line
        self._buffer = ""

    def write(self, text: str) -> int:
        self._buffer += text
        while True:
            match = re.search(r"[\r\n]", self._buffer)
            if not match:
                break
            line, self._buffer = self._buffer[: match.start()], self._buffer[match.end() :]
            if line.strip():
                self._handle_line(line)
        return len(text)

    def flush(self) -> None:
        if self._buffer.strip():
            self._handle_line(self._buffer)
        self._buffer = ""


def run_transcribe(args: argparse.Namespace) -> None:
    from .. import layout
    from ..export import probe_video
    from ..transcribe import transcribe_video, write_outputs

    video = Path(args.video)
    total_sec = probe_video(video)["duration"] or 0
    emit({"event": "progress", "percent": 0, "stage": "transcribe", "detail": f"loading {args.model}…"})

    def handle_line(line: str) -> None:
        match = SEGMENT_END_RE.search(line)
        if match and total_sec:
            hours, minutes, seconds, millis = match.groups()
            end_sec = int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000
            percent = min(round(end_sec / total_sec * 100, 1), 100)
            emit({"event": "progress", "percent": percent, "stage": "transcribe", "detail": line.strip()})
        emit({"event": "log", "line": line})

    with contextlib.redirect_stdout(_LineWriter(handle_line)):
        result = transcribe_video(video, model=args.model, language=args.language, verbose=True)
    out_dir = Path(args.out_dir) if args.out_dir else layout.source_dir(video)
    paths = write_outputs(result, video, out_dir)
    emit({"event": "result", "paths": {name: str(path) for name, path in paths.items()}})


def run_convert(args: argparse.Namespace) -> None:
    from ..playable import convert

    video, out = Path(args.video), Path(args.out)
    emit({"event": "progress", "percent": 0, "stage": "convert",
          "detail": f"making a browser-playable copy of {video.name}…"})
    last = {"pct": -5.0}

    def on_progress(pct: float) -> None:
        if pct - last["pct"] >= 1:  # don't flood the stream at ffmpeg speed
            last["pct"] = pct
            emit({"event": "progress", "percent": round(pct, 1), "stage": "convert",
                  "detail": f"writing {out.name}… {pct:.0f}%"})

    convert(video, out, on_progress=on_progress, on_log=lambda line: emit({"event": "log", "line": line}))
    emit({"event": "result", "paths": {"playable": str(out)}})


def run_highlights(args: argparse.Namespace) -> None:
    from ..highlights import select_highlights, write_highlights

    segments_path = Path(args.segments)
    data = json.loads(segments_path.read_text(encoding="utf-8"))
    segments = data["segments"]
    if not segments:
        raise RuntimeError(f"{segments_path.name} contains no segments")

    emit({"event": "progress", "percent": None, "stage": "gemini",
          "detail": f"asking {args.gemini_model} for up to {args.count} highlights…"})
    highlights = select_highlights(
        segments,
        count=args.count,
        min_duration=args.min_duration,
        max_duration=args.max_duration,
        gemini_model=args.gemini_model,
    )
    stem = segments_path.name.removesuffix(".segments.json")
    paths = write_highlights(
        highlights,
        video_name=data.get("video", stem),
        stem=stem,
        out_dir=segments_path.parent,
        gemini_model=args.gemini_model,
    )
    for i, h in enumerate(highlights, 1):
        emit({"event": "log", "line": f"{i}. [{h['start']} → {h['end']}] ({h['virality_score']}) {h['title']}"})
    emit({"event": "result", "count": len(highlights),
          "paths": {name: str(path) for name, path in paths.items()}})


def run_captions(args: argparse.Namespace) -> None:
    from .. import layout
    from ..captions import generate_captions, proofread_captions, write_captions

    clip = Path(args.clip)
    emit({"event": "progress", "percent": None, "stage": "whisperx",
          "detail": f"word-level captions for {clip.name} ({args.model}, CPU — about a minute per clip)…"})
    with contextlib.redirect_stdout(_LineWriter(lambda line: emit({"event": "log", "line": line}))):
        captions = generate_captions(clip, model=args.model, language=args.language)
    emit({"event": "log", "line": f"{len(captions)} words"})

    corrections: list = []
    proofread_ran = False
    if args.proofread and (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        emit({"event": "progress", "percent": None, "stage": "proofread",
              "detail": f"proofreading with {args.gemini_model}…"})
        try:
            corrections = proofread_captions(captions, gemini_model=args.gemini_model)
            proofread_ran = True
        except Exception as exc:
            emit({"event": "log", "line": f"grammar check failed ({exc}) — keeping the raw transcript"})
        for index, before, after, reason in corrections:
            emit({"event": "log", "line": f"  {index}: {before} → {after}  ({reason})"})

    emit({"event": "progress", "percent": None, "stage": "write", "detail": "writing captions…"})
    paths = write_captions(captions, clip, copy_to_app=True)
    correction_dicts = [{"index": i, "before": b, "after": a, "reason": r} for i, b, a, r in corrections]
    if proofread_ran:
        # persist so the web UI can show the proofread results on any later visit
        corr_path = layout.ensure_parent(layout.sidecar(clip, "corrections.json"))
        corr_path.write_text(
            json.dumps({"gemini_model": args.gemini_model, "corrections": correction_dicts},
                       indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        paths["corrections"] = corr_path
    emit({"event": "result", "corrections": correction_dicts,
          "paths": {name: str(path) for name, path in paths.items()}})
    # whisperx leaves non-daemon threads behind that block a normal exit
    _REAL_STDOUT.flush()
    sys.stderr.flush()
    os._exit(0)


def run_export_vertical(args: argparse.Namespace) -> None:
    from ..vertical import export_vertical_clip

    video = Path(args.video)
    out = Path(args.out)
    hook = (args.hook_start, args.hook_end) if args.hook_start is not None and args.hook_end is not None else None
    opening = f" (opening with the {args.hook_end - args.hook_start:.0f}s hook)" if hook else ""
    emit({"event": "progress", "percent": None, "stage": "tracking",
          "detail": f"tracking the speaker in {args.start:.0f}s–{args.end:.0f}s{opening} (Apple Vision)…"})
    last = {"pct": -5.0}

    def on_progress(pct: float) -> None:
        if pct - last["pct"] >= 1:  # don't flood the stream at ffmpeg speed
            last["pct"] = pct
            emit({"event": "progress", "percent": round(pct, 1), "stage": "encoding",
                  "detail": f"rendering the vertical clip… {pct:.0f}%"})

    with contextlib.redirect_stdout(_LineWriter(lambda line: emit({"event": "log", "line": line}))):
        export_vertical_clip(video, args.start, args.end, out, on_progress=on_progress, hook=hook)
    emit({"event": "result", "paths": {"vertical": str(out)}})


def run_track(args: argparse.Namespace) -> None:
    from ..track import track_video

    clip = Path(args.clip)
    emit({"event": "progress", "percent": None, "stage": "track",
          "detail": f"tracking the speaker in {clip.name} (Apple Vision)…"})
    with contextlib.redirect_stdout(_LineWriter(lambda line: emit({"event": "log", "line": line}))):
        paths = track_video(clip, copy_to_app=True)
    emit({"event": "result", "paths": {name: str(path) for name, path in paths.items()}})


def main() -> None:
    from ..cli import _load_dotenv

    _load_dotenv()
    parser = argparse.ArgumentParser(prog="sermon-worker")
    sub = parser.add_subparsers(dest="kind", required=True)

    p = sub.add_parser("transcribe")
    p.add_argument("--video", required=True)
    p.add_argument("--model", default="large-v3-turbo")
    p.add_argument("--language", default="sk")
    p.add_argument("--out-dir", default=None)
    p.set_defaults(func=run_transcribe)

    p = sub.add_parser("convert")
    p.add_argument("--video", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=run_convert)

    p = sub.add_parser("highlights")
    p.add_argument("--segments", required=True)
    p.add_argument("--count", type=int, default=8)
    p.add_argument("--min-duration", type=int, default=20)
    p.add_argument("--max-duration", type=int, default=100)
    p.add_argument("--gemini-model", default="gemini-flash-latest")
    p.set_defaults(func=run_highlights)

    p = sub.add_parser("captions")
    p.add_argument("--clip", required=True)
    p.add_argument("--model", default="large-v3-turbo")
    p.add_argument("--language", default="sk")
    p.add_argument("--gemini-model", default="gemini-flash-latest")
    p.add_argument("--no-proofread", dest="proofread", action="store_false")
    p.set_defaults(func=run_captions)

    p = sub.add_parser("track")
    p.add_argument("--clip", required=True)
    p.set_defaults(func=run_track)

    p = sub.add_parser("export-vertical")
    p.add_argument("--video", required=True)
    p.add_argument("--start", type=float, required=True)
    p.add_argument("--end", type=float, required=True)
    # both present: cut this window in front of the passage (hook-first clip)
    p.add_argument("--hook-start", type=float, default=None)
    p.add_argument("--hook-end", type=float, default=None)
    p.add_argument("--out", required=True)
    p.set_defaults(func=run_export_vertical)

    args = parser.parse_args()
    try:
        args.func(args)
    except BaseException as exc:  # SystemExit included — the pipeline uses it as an error channel
        if isinstance(exc, KeyboardInterrupt):
            raise
        emit({"event": "error", "message": str(exc) or exc.__class__.__name__})
        sys.exit(1)


if __name__ == "__main__":
    main()
