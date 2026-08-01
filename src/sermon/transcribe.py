"""Local transcription of sermon videos via mlx-whisper (Metal GPU on Apple Silicon)."""

import json
from pathlib import Path

DEFAULT_MODEL = "large-v3-turbo"

MODEL_ALIASES = {
    "tiny": "mlx-community/whisper-tiny",
    "small": "mlx-community/whisper-small-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "large": "mlx-community/whisper-large-v3-mlx",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "turbo": "mlx-community/whisper-large-v3-turbo",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
}


def resolve_model(name: str) -> str:
    """Map a short model name to its Hugging Face repo; pass full repo ids through."""
    return MODEL_ALIASES.get(name, name)


def format_timestamp(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def format_srt_timestamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    return f"{ms // 3_600_000:02d}:{ms % 3_600_000 // 60_000:02d}:{ms % 60_000 // 1000:02d},{ms % 1000:03d}"


def transcribe_video(
    video: Path,
    model: str = DEFAULT_MODEL,
    language: str = "sk",
    verbose: bool = False,
) -> dict:
    """Transcribe a video/audio file. ffmpeg (invoked by mlx-whisper) handles decoding,
    so any container with an audio track works directly."""
    import mlx_whisper  # deferred: importing mlx is slow

    result = mlx_whisper.transcribe(
        str(video),
        path_or_hf_repo=resolve_model(model),
        language=language,
        # verbose=False shows a progress bar; True prints every segment as it decodes
        verbose=True if verbose else False,
    )
    result["model"] = model
    return result


def write_outputs(result: dict, video: Path, out_dir: Path) -> dict[str, Path]:
    """Write <stem>.transcript.txt, <stem>.srt and <stem>.segments.json; return the paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = video.stem
    segments = [
        {"start": round(s["start"], 2), "end": round(s["end"], 2), "text": s["text"].strip()}
        for s in result["segments"]
        if s["text"].strip()
    ]

    txt_path = out_dir / f"{stem}.transcript.txt"
    txt_path.write_text(
        "\n".join(f"[{format_timestamp(s['start'])}] {s['text']}" for s in segments) + "\n",
        encoding="utf-8",
    )

    srt_path = out_dir / f"{stem}.srt"
    srt_lines = []
    for i, s in enumerate(segments, 1):
        srt_lines += [
            str(i),
            f"{format_srt_timestamp(s['start'])} --> {format_srt_timestamp(s['end'])}",
            s["text"],
            "",
        ]
    srt_path.write_text("\n".join(srt_lines), encoding="utf-8")

    json_path = out_dir / f"{stem}.segments.json"
    json_path.write_text(
        json.dumps(
            {
                "video": video.name,
                "language": result.get("language"),
                "model": result.get("model"),
                "duration": segments[-1]["end"] if segments else 0.0,
                "segments": segments,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    return {"transcript": txt_path, "srt": srt_path, "segments": json_path}
