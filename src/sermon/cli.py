"""CLI: transcribe sermon videos locally and select social-media highlights with Gemini."""

import argparse
import json
import os
import sys
import time
from pathlib import Path

from . import __version__
from .export import export_timeline
from .highlights import DEFAULT_GEMINI_MODEL, select_highlights, write_highlights
from .transcribe import DEFAULT_MODEL, MODEL_ALIASES, transcribe_video, write_outputs


def _load_dotenv() -> None:
    """Load KEY=value pairs from ./.env or the project root, without overriding the environment."""
    candidates = [Path.cwd() / ".env", Path(__file__).resolve().parents[2] / ".env"]
    for env_file in candidates:
        if not env_file.is_file():
            continue
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def main(argv: list[str] | None = None) -> None:
    _load_dotenv()
    parser = argparse.ArgumentParser(
        prog="sermon",
        description="Transcribe Slovak sermon videos locally (mlx-whisper on the Apple GPU) "
        "and pick social-media highlight moments with Gemini.",
    )
    parser.add_argument("--version", action="version", version=f"sermon {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_transcribe = sub.add_parser("transcribe", help="transcribe a video to .transcript.txt / .srt / .segments.json")
    _add_transcribe_args(p_transcribe)

    p_highlights = sub.add_parser("highlights", help="pick highlights from an existing transcript with Gemini")
    p_highlights.add_argument("input", type=Path, help="video file (finds its .segments.json) or a .segments.json directly")
    _add_highlight_args(p_highlights)

    p_export = sub.add_parser("export", help="export highlights as an XML timeline for DaVinci Resolve")
    p_export.add_argument("input", type=Path, help="video file (finds its .highlights.json) or a .highlights.json directly")

    p_captions = sub.add_parser(
        "captions", help="word-level captions for a rendered clip (WhisperX) + hand-off to the Remotion app"
    )
    p_captions.add_argument("video", type=Path, help="rendered clip from DaVinci")
    p_captions.add_argument("-m", "--model", default="large-v3-turbo", help="whisper model (default: large-v3-turbo)")
    p_captions.add_argument("-l", "--language", default="sk", help="spoken language code (default: sk)")
    p_captions.add_argument("--no-copy", action="store_true", help="don't copy the clip + JSON into captions/public/")
    p_captions.add_argument("--no-grammar-check", action="store_true", help="skip the Gemini proofread of the transcript")
    p_captions.add_argument(
        "--gemini-model",
        default=DEFAULT_GEMINI_MODEL,
        help=f"Gemini model for the proofread (default: {DEFAULT_GEMINI_MODEL})",
    )
    p_captions.add_argument("--no-track", action="store_true", help="skip subject tracking for the 9:16 crop")

    p_track = sub.add_parser(
        "track", help="track the speaker (Apple Vision) and write X offsets for the 9:16 crop"
    )
    p_track.add_argument("video", type=Path, help="rendered clip from DaVinci")
    p_track.add_argument("--no-copy", action="store_true", help="don't copy the framing JSON into captions/public/")
    p_track.add_argument("--debug", action="store_true", help="also render a preview with the crop window burned in")

    p_web = sub.add_parser("web", help="start the local web UI (guided pipeline in the browser)")
    p_web.add_argument("--port", type=int, default=8756, help="port to listen on (default: 8756)")
    p_web.add_argument("--no-browser", action="store_true", help="don't open the browser automatically")

    p_run = sub.add_parser("run", help="transcribe + highlights in one go")
    _add_transcribe_args(p_run)
    _add_highlight_args(p_run)
    p_run.add_argument("--force", action="store_true", help="re-transcribe even if a .segments.json already exists")

    args = parser.parse_args(argv)

    if args.command == "transcribe":
        _cmd_transcribe(args)
    elif args.command == "highlights":
        _cmd_highlights(args)
    elif args.command == "export":
        _cmd_export(args)
    elif args.command == "captions":
        _cmd_captions(args)
    elif args.command == "track":
        _cmd_track(args)
    elif args.command == "web":
        _cmd_web(args)
    else:
        _cmd_run(args)


def _add_transcribe_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("video", type=Path, help="input video/audio file")
    p.add_argument("-o", "--output-dir", type=Path, default=None, help="output directory (default: next to the video)")
    p.add_argument(
        "-m",
        "--model",
        default=DEFAULT_MODEL,
        help=f"whisper model: {', '.join(sorted(set(MODEL_ALIASES)))} or a Hugging Face repo id (default: {DEFAULT_MODEL})",
    )
    p.add_argument("-l", "--language", default="sk", help="spoken language code (default: sk)")
    p.add_argument("--verbose", action="store_true", help="print each segment while transcribing")


def _add_highlight_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("-n", "--count", type=int, default=8, help="number of highlights to request (default: 8)")
    p.add_argument("--min-duration", type=int, default=20, help="minimum clip length in seconds (default: 20)")
    p.add_argument(
        "--max-duration",
        type=int,
        default=110,
        help="hard cap on clip length in seconds; the prompt targets 30-90 s regardless (default: 110)",
    )
    p.add_argument(
        "--gemini-model",
        default=DEFAULT_GEMINI_MODEL,
        help=f"Gemini model id (default: {DEFAULT_GEMINI_MODEL})",
    )


def _cmd_transcribe(args: argparse.Namespace) -> Path:
    video: Path = args.video
    if not video.exists():
        sys.exit(f"error: {video} does not exist")
    out_dir = args.output_dir or video.resolve().parent

    print(f"Transcribing {video.name} with {args.model} (language: {args.language})…")
    started = time.monotonic()
    result = transcribe_video(video, model=args.model, language=args.language, verbose=args.verbose)
    elapsed = time.monotonic() - started

    paths = write_outputs(result, video, out_dir)
    print(f"Done in {elapsed / 60:.1f} min.")
    for name, path in paths.items():
        print(f"  {name}: {path}")
    return paths["segments"]


def _cmd_highlights(args: argparse.Namespace, segments_path: Path | None = None) -> None:
    if segments_path is None:
        segments_path = _resolve_segments_path(args.input)

    data = json.loads(segments_path.read_text(encoding="utf-8"))
    segments = data["segments"]
    if not segments:
        sys.exit(f"error: {segments_path} contains no segments")

    print(f"Selecting up to {args.count} highlights with {args.gemini_model}…")
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
    print(f"{len(highlights)} highlights:")
    for i, h in enumerate(highlights, 1):
        print(f"  {i}. [{h['start']} → {h['end']}] ({h['virality_score']}) {h['title']}")
    for name, path in paths.items():
        print(f"  {name}: {path}")

    video_file = segments_path.parent / data.get("video", "")
    if video_file.is_file():
        xml_path = export_timeline(paths["json"], video_file)
        print(f"  timeline: {xml_path}  (DaVinci Resolve: File → Import Timeline)")
    else:
        print(f"  (video not found next to the transcript — run `sermon export <video>` for the Resolve XML)")


def _cmd_export(args: argparse.Namespace) -> None:
    input_path: Path = args.input
    if input_path.name.endswith(".highlights.json"):
        hl_path = input_path.resolve()
        video = hl_path.parent / json.loads(hl_path.read_text(encoding="utf-8")).get("video", "")
    else:
        video = input_path.resolve()
        hl_path = video.parent / f"{video.stem}.highlights.json"
    if not hl_path.exists():
        sys.exit(f"error: {hl_path} not found — run `sermon highlights` first")
    if not video.is_file():
        sys.exit(f"error: video file {video} not found (needed for frame rate and media link)")
    xml_path = export_timeline(hl_path, video)
    print(f"Timeline written: {xml_path}")
    print("Import in DaVinci Resolve: File → Import Timeline → Import AAF, EDL, XML…")


def _cmd_run(args: argparse.Namespace) -> None:
    video: Path = args.video
    if not video.exists():
        sys.exit(f"error: {video} does not exist")
    out_dir = args.output_dir or video.resolve().parent
    segments_path = out_dir / f"{video.stem}.segments.json"

    if segments_path.exists() and not args.force:
        print(f"Reusing existing {segments_path.name} (use --force to re-transcribe).")
    else:
        segments_path = _cmd_transcribe(args)

    _cmd_highlights(args, segments_path=segments_path)


def _cmd_captions(args: argparse.Namespace) -> None:
    from .captions import generate_captions, proofread_captions, write_captions

    video: Path = args.video
    if not video.exists():
        sys.exit(f"error: {video} does not exist")

    print(f"Generating word-level captions for {video.name} (WhisperX, {args.model}, {args.language})…")
    started = time.monotonic()
    captions = generate_captions(video, model=args.model, language=args.language)
    print(f"{len(captions)} words in {time.monotonic() - started:.0f} s.")

    if args.no_grammar_check:
        pass
    elif not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        print("GEMINI_API_KEY not set — skipping the grammar check")
    else:
        print(f"Proofreading transcript with {args.gemini_model}…")
        try:
            fixes = proofread_captions(captions, gemini_model=args.gemini_model)
        except Exception as exc:
            print(f"  grammar check failed ({exc}) — keeping the raw transcript")
            fixes = []
        for index, before, after, reason in fixes:
            print(f"  {index}: {before} → {after}  ({reason})")
        if not fixes:
            print("  no corrections needed")

    paths = write_captions(captions, video, copy_to_app=not args.no_copy)
    print(f"  captions: {paths['captions']}")

    if not args.no_track and _is_vertical(video):
        print("Clip is already vertical (≤ 9:16) — skipping speaker tracking.")
    elif not args.no_track:
        from .track import track_video

        print("Tracking the speaker for the 9:16 crop (Apple Vision)…")
        try:
            track_paths = track_video(video, copy_to_app=not args.no_copy)
            print(f"  framing: {track_paths['framing']}")
        except Exception as exc:
            print(f"  tracking failed ({exc}) — the crop stays centered")

    if "app" in paths:
        print(f"  copied into captions app: {paths['app'].parent}")
        print(f"  → fix any typos in {paths['captions'].name}, then: cd captions && npm run studio")
    # whisperx leaves non-daemon threads behind that block a normal exit
    sys.stdout.flush()
    os._exit(0)


def _cmd_web(args: argparse.Namespace) -> None:
    try:
        from .web.app import run_server
    except ImportError as exc:
        sys.exit(f"error: web dependencies missing ({exc}) — run `uv sync`")
    run_server(port=args.port, open_browser=not args.no_browser)


def _is_vertical(video: Path) -> bool:
    """True when the clip needs no 9:16 reframe (vertical-first exports)."""
    from .export import probe_video

    try:
        meta = probe_video(video)
        return meta["width"] / meta["height"] <= 9 / 16 * 1.02
    except Exception:
        return False


def _cmd_track(args: argparse.Namespace) -> None:
    from .track import track_video

    video: Path = args.video
    if not video.exists():
        sys.exit(f"error: {video} does not exist")

    print(f"Tracking the speaker in {video.name} (Apple Vision)…")
    started = time.monotonic()
    paths = track_video(video, copy_to_app=not args.no_copy, debug=args.debug)
    print(f"Done in {time.monotonic() - started:.0f} s.")
    for name, path in paths.items():
        print(f"  {name}: {path}")


def _resolve_segments_path(input_path: Path) -> Path:
    if input_path.name.endswith(".segments.json"):
        segments_path = input_path
    else:
        segments_path = input_path.resolve().parent / f"{input_path.stem}.segments.json"
        if not segments_path.exists():
            sys.exit(f"error: {segments_path} not found — run `sermon transcribe {input_path.name}` first")
    if not segments_path.exists():
        sys.exit(f"error: {segments_path} does not exist")
    return segments_path


if __name__ == "__main__":
    main()
