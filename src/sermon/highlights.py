"""Highlight selection from a timestamped transcript via the Gemini API."""

import json
import os
from pathlib import Path

from pydantic import BaseModel, Field

from .transcribe import format_timestamp

DEFAULT_GEMINI_MODEL = "gemini-flash-latest"


class Highlight(BaseModel):
    start: str = Field(description="Start timestamp HH:MM:SS, copied exactly from a transcript line")
    end: str = Field(description="End timestamp HH:MM:SS")
    hook_start: str = Field(description="Start timestamp HH:MM:SS of the hook moment, copied from a transcript line")
    hook_end: str = Field(description="End timestamp HH:MM:SS of the hook moment")
    title: str = Field(description="Short catchy title in Slovak")
    summary: str = Field(description="1-2 sentence summary in Slovak")
    hook_text: str = Field(description="The hook sentence(s), verbatim in Slovak")
    hook_score: int = Field(description="1-100: would the first 3 seconds stop a stranger's scroll?")
    flow_score: int = Field(description="1-100: one self-contained thought with a satisfying landing?")
    value_score: int = Field(description="1-100: emotional or practical payoff for the viewer?")
    reach_score: int = Field(description="1-100: resonates beyond churchgoers, felt-need topic, shareable?")
    score_reason: str = Field(description="One short sentence in Slovak naming the clip's strongest and weakest aspect")


class HighlightList(BaseModel):
    highlights: list[Highlight]


PROMPT_TEMPLATE = """\
You are selecting short social-media highlight clips (Instagram Reels / YouTube Shorts) \
from a church sermon held in Slovak.

Below is the full transcript. Each line starts with the [HH:MM:SS] timestamp at which it is spoken.

Select the {count} strongest moments. Each highlight must:
- be self-contained and understandable for someone who has not heard the rest of the sermon
- start exactly at a timestamp that appears in the transcript, at a natural sentence start
- end at a natural closing point, not mid-thought
- not overlap with any other highlight

SELECTION GUIDELINES — what performs for sermon clips on Reels/Shorts/TikTok:
- one complete idea per clip, never two
- cut straight into the moment: no setup, no introductions, no wind-down
- personal stories and concrete illustrations outperform abstract theology
- felt-need topics travel furthest: identity, anxiety, relationships, purpose, failure, hope
- language a non-churchgoer understands; avoid insider jargon unless the clip itself explains it
- emotional peaks perform: laughter, vulnerability, conviction, comfort
- prefer clips that end on a "screenshot sentence" — the line people quote in their captions

Duration rules — shorter is better:
- cut at the first natural endpoint after the core message lands; do not pad
- target 30-90 seconds
- hard maximum {max_duration} seconds: exceed 90 only when the thought genuinely cannot close earlier
- minimum {min_duration} seconds

For each highlight also pick a HOOK: the single most attention-grabbing moment of roughly 3-10 seconds
from within the clip — a bold claim, a provocative question, or an emotional line that works with zero
context. The final video will open with the hook and then play the full clip, so prefer a hook that is
NOT the clip's opening sentence (avoids an immediate repeat). hook_start and hook_end must also be
timestamps that appear in the transcript.

Score each highlight in four categories (1-100 each). Be discriminating: use the full scale,
spread the scores across candidates, and reserve 90+ for a moment you would bet on going viral.
- hook_score: would the first 3 seconds (the HOOK you chose) stop a stranger's scroll?
- flow_score: does it play as one self-contained thought that builds and lands cleanly?
- value_score: does the viewer leave with something — comfort, insight, conviction, a laugh?
- reach_score: does it work for someone who never enters a church (felt-need topic, no jargon)?
Give score_reason: one Slovak sentence naming the clip's strongest and weakest aspect.

Write title, summary, and hook_text in Slovak. Order the highlights strongest first.

TRANSCRIPT:
{transcript}
"""


def parse_timestamp(value: str) -> float:
    seconds = 0.0
    for part in value.strip().split(":"):
        seconds = seconds * 60 + float(part)
    return seconds


def select_highlights(
    segments: list[dict],
    count: int = 8,
    min_duration: int = 30,
    max_duration: int = 90,
    gemini_model: str = DEFAULT_GEMINI_MODEL,
) -> list[dict]:
    from google import genai  # deferred so transcribe-only runs don't need it configured
    from google.genai import types

    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        raise SystemExit(
            "GEMINI_API_KEY is not set.\n"
            "Get a key at https://aistudio.google.com/apikey and run: export GEMINI_API_KEY=..."
        )

    transcript = "\n".join(f"[{format_timestamp(s['start'])}] {s['text']}" for s in segments)
    prompt = PROMPT_TEMPLATE.format(
        count=count,
        min_duration=min_duration,
        max_duration=max_duration,
        transcript=transcript,
    )

    client = genai.Client()
    response = client.models.generate_content(
        model=gemini_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=HighlightList,
        ),
    )
    parsed = response.parsed
    if not isinstance(parsed, HighlightList):
        raise SystemExit(f"Gemini returned an unparseable response:\n{response.text}")

    finalized = [_finalize(h, segments) for h in parsed.highlights]
    finalized.sort(key=lambda h: h["virality_score"], reverse=True)
    return finalized


def _snap_range(start_raw: str, end_raw: str, segments: list[dict]) -> tuple[float, float]:
    """Snap a start/end timestamp pair to real segment boundaries."""
    start = min((s["start"] for s in segments), key=lambda t: abs(t - parse_timestamp(start_raw)))
    end = min((s["end"] for s in segments), key=lambda t: abs(t - parse_timestamp(end_raw)))
    if end <= start:
        end = next((s["end"] for s in segments if s["end"] > start), start)
    return start, end


def _finalize(h: Highlight, segments: list[dict]) -> dict:
    """Snap Gemini's timestamps to real segment boundaries and attach the transcript excerpt."""
    start, end = _snap_range(h.start, h.end, segments)
    hook_start, hook_end = _snap_range(h.hook_start, h.hook_end, segments)

    excerpt = " ".join(
        s["text"] for s in segments if s["start"] >= start - 0.5 and s["end"] <= end + 0.5
    )
    return {
        "start": format_timestamp(start),
        "end": format_timestamp(end),
        "start_sec": start,
        "end_sec": end,
        "duration_sec": round(end - start, 1),
        "hook_start": format_timestamp(hook_start),
        "hook_end": format_timestamp(hook_end),
        "hook_start_sec": hook_start,
        "hook_end_sec": hook_end,
        "hook_duration_sec": round(hook_end - hook_start, 1),
        "title": h.title,
        "summary": h.summary,
        "hook_text": h.hook_text,
        # hook-weighted composite, mirroring OpusClip's Hook/Flow/Value/Trend model
        "virality_score": round(
            0.35 * h.hook_score + 0.20 * h.flow_score + 0.25 * h.value_score + 0.20 * h.reach_score
        ),
        "scores": {
            "hook": h.hook_score,
            "flow": h.flow_score,
            "value": h.value_score,
            "reach": h.reach_score,
        },
        "score_reason": h.score_reason,
        "excerpt": excerpt,
    }


def write_highlights(
    highlights: list[dict],
    video_name: str,
    stem: str,
    out_dir: Path,
    gemini_model: str,
) -> dict[str, Path]:
    """Write <stem>.highlights.json and <stem>.highlights.md; return the paths."""
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / f"{stem}.highlights.json"
    json_path.write_text(
        json.dumps(
            {"video": video_name, "gemini_model": gemini_model, "highlights": highlights},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    lines = [f"# Highlights — {video_name}", ""]
    for i, h in enumerate(highlights, 1):
        lines += [
            f"## {i}. {h['title']}",
            "",
            f"**Čas:** {h['start']} → {h['end']} ({h['duration_sec']:.0f} s)",
            "",
            f"**Hook:** {h['hook_start']} → {h['hook_end']} ({h['hook_duration_sec']:.0f} s)",
            "",
            f"**Skóre:** {h['virality_score']}/100 "
            f"(hook {h['scores']['hook']} · flow {h['scores']['flow']} · "
            f"hodnota {h['scores']['value']} · dosah {h['scores']['reach']}) — {h['score_reason']}",
            "",
            h["summary"],
            "",
            f"> {h['hook_text']}",
            "",
            "<details><summary>Prepis úseku</summary>",
            "",
            h["excerpt"],
            "",
            "</details>",
            "",
        ]
    md_path = out_dir / f"{stem}.highlights.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")

    return {"json": json_path, "markdown": md_path}
