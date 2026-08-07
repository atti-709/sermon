"""Getting a video into a container the rest of the pipeline can actually open.

ffmpeg reads anything, so transcription, tracking and the vertical export never
care what they are handed. Two consumers do:

- **a browser** — the highlight preview, the Remotion Studio/Player preview and
  the WebCodecs-based render all decode through Chrome. Chrome plays h264, hevc,
  vp9 and av1, but only out of an MP4/MOV-shaped container: Matroska is refused
  outright, however ordinary the video inside it is.
- **DaVinci Resolve** — it links the source file named in the Resolve XML, and
  its importer has never read Matroska either.

Which is the whole problem with a `.mkv`: an OBS recording is already h264/aac,
the bytes a browser wants are right there, wrapped in the one container it will
not open. So the fix is a **remux** — copy the streams into an MP4 without
touching them. It runs at disk speed (a 45-minute sermon in seconds), it is
bit-exact, and the result is the same size as the source. A real transcode only
happens when the video codec itself is undecodable (a ProRes DaVinci master, a
DNxHD or FFV1 recording), because then there is nothing to copy.

The codec is what decides, never whether ffmpeg agrees to write the file:
ffmpeg will happily mux FFV1 into an `.mp4` and hand you something no browser
can play.
"""

import subprocess
from pathlib import Path
from typing import Callable

# what Chrome can decode — the gate that decides remux vs. transcode
WEB_SAFE_VIDEO_CODECS = frozenset({"h264", "hevc", "vp9", "av1"})

# containers a browser and Resolve both open. `.mov` is on the list because the
# vertical export writes one, and re-wrapping it would be pure waste.
PLAYABLE_SUFFIXES = frozenset({".mp4", ".mov", ".m4v"})

# audio that can move into an MP4 as-is. Anything else (the PCM an OBS recording
# or a mezzanine export carries) is re-encoded to AAC — seconds for a whole
# sermon, and it keeps the video copy untouched.
COPYABLE_AUDIO_CODECS = frozenset({"aac", "mp3", "alac"})

TARGET_SUFFIX = ".mp4"


def playable_name(name: str) -> str:
    """`name` under an extension a browser will open — unchanged when it already has one."""
    return name if Path(name).suffix.lower() in PLAYABLE_SUFFIXES else Path(name).stem + TARGET_SUFFIX


def codecs(video: Path) -> tuple[str, str]:
    """(video codec, audio codec) of the first stream of each; "" when there is none."""
    import json

    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name",
         "-of", "json", str(video)],
        capture_output=True, text=True, check=False,
    ).stdout
    try:
        streams = json.loads(out).get("streams", [])
    except json.JSONDecodeError:
        return "", ""
    first = lambda kind: next((s.get("codec_name", "") for s in streams if s.get("codec_type") == kind), "")
    return first("video"), first("audio")


def is_playable(video: Path) -> bool:
    """True when a browser and Resolve can both open this file as it stands.

    Audio deliberately doesn't count. The vertical export writes h264 video with
    PCM audio into a `.mov` and that has always previewed fine; making audio part
    of the test would send every one of those clips through a transcode it does
    not need."""
    if video.suffix.lower() not in PLAYABLE_SUFFIXES:
        return False
    return codecs(video)[0] in WEB_SAFE_VIDEO_CODECS


def _duration(video: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
        capture_output=True, text=True, check=False,
    ).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def convert(
    video: Path,
    out: Path,
    on_progress: Callable[[float], None] | None = None,
    on_log: Callable[[str], None] | None = None,
) -> Path:
    """Write `video` to `out` as something a browser and Resolve can open.

    Streams are copied wherever they can be — a remux of an h264 `.mkv` is pure
    I/O and finishes in seconds. Only a video codec Chrome cannot decode forces a
    re-encode. Subtitle and attachment streams are dropped: Matroska carries
    formats MP4 has no box for, and one of them would fail the whole mux."""
    video, out = video.resolve(), out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    vcodec, acodec = codecs(video)
    log = on_log or (lambda line: None)

    if vcodec in WEB_SAFE_VIDEO_CODECS:
        video_args = ["-c:v", "copy"]
        log(f"{vcodec} is already browser-playable — copying the stream into {TARGET_SUFFIX} (no re-encode)")
    else:
        video_args = ["-c:v", "h264_videotoolbox", "-b:v", "30M"]
        log(f"{vcodec or 'this video'} is not browser-playable — re-encoding to h264 at 30 Mbps")

    if not acodec:
        audio_args = ["-an"]
    elif acodec in COPYABLE_AUDIO_CODECS:
        audio_args = ["-c:a", "copy"]
    else:
        audio_args = ["-c:a", "aac", "-b:a", "256k"]
        log(f"{acodec} audio cannot live in an {TARGET_SUFFIX} — re-encoding it to AAC")

    total = _duration(video)
    argv = [
        "ffmpeg", "-v", "error", "-y", "-i", str(video),
        # the first video and audio stream only: subtitles and cover art have no
        # MP4 equivalent, and the rest of the pipeline reads no further either
        "-map", "0:v:0", "-map", "0:a:0?",
        *video_args, *audio_args,
        "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats",
        str(out),
    ]
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert proc.stdout is not None
    for line in proc.stdout:
        if line.startswith("out_time_ms=") and on_progress and total:
            try:
                on_progress(min(int(line.split("=", 1)[1]) / 1e6 / total * 100, 100.0))
            except ValueError:
                pass
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {(proc.stderr.read() if proc.stderr else '').strip()}")
    return out


def ensure(video: Path, out: Path, on_log: Callable[[str], None] | None = None) -> Path:
    """The path to open `video` by: itself when it needs nothing, else `out`, built if missing."""
    if is_playable(video):
        return video
    if not out.is_file():
        (on_log or print)(f"  {video.suffix} is not readable by a browser or Resolve — writing {out.name}…")
        convert(video, out, on_log=lambda line: (on_log or print)(f"  {line}"))
    return out
