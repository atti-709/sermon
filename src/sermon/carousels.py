"""Instagram carousel suggestions from a timestamped transcript via the Gemini API.

A carousel is a multi-slide text post: a cover line that stops the scroll, one
thought per slide, and a closing takeaway. Gemini writes the slide texts from the
sermon's own words — the output of this step is text to lay onto the brand
template, not video."""

import json
import os
from pathlib import Path

from pydantic import BaseModel, Field

from .highlights import DEFAULT_GEMINI_MODEL, parse_timestamp
from .transcribe import format_timestamp


class Carousel(BaseModel):
    title: str = Field(description="Short internal title in Slovak (never shown on a slide)")
    source_start: str = Field(
        description="Start timestamp HH:MM:SS of the passage the carousel draws from, copied exactly from a transcript line"
    )
    source_end: str = Field(description="End timestamp HH:MM:SS of that passage")
    slides: list[str] = Field(
        description="The slide texts in order: cover line first, one thought per slide, closing takeaway + call to action last"
    )
    caption: str = Field(
        description="Instagram caption in Slovak, 1-3 sentences ending with a question that invites comments; no hashtags"
    )
    hashtags: list[str] = Field(description="5-8 lowercase hashtags without the # sign")
    save_score: int = Field(description="1-100: would a stranger save or share this carousel?")
    score_reason: str = Field(
        description="One short sentence in Slovak naming the carousel's strongest and weakest aspect"
    )


class CarouselList(BaseModel):
    carousels: list[Carousel]


PROMPT_TEMPLATE = """\
You are creating Instagram CAROUSEL posts (multi-slide text posts) from a church sermon \
held in Slovak.

Below is the full transcript. Each line starts with the [HH:MM:SS] timestamp at which it is spoken.

Create the {count} strongest carousels. A carousel is a sequence of exactly {frames} slides — \
short texts a reader swipes through. Each carousel must:
- be built around ONE idea from the sermon, self-contained for someone who never heard the rest
- draw on a single passage of the sermon; copy source_start / source_end from transcript lines
- read as the speaker's message, not commentary about it — keep the voice, sharpen the words
- not reuse the core idea of another carousel in the set

SLIDE STRUCTURE — exactly {frames} slides per carousel, in this shape:
- Slide 1 is the cover: one bold line of at most 10 words that makes a stranger swipe —
  a claim, a question, or a tension. No emoji, no "1/{frames}", no hashtags.
- Every middle slide carries ONE thought in 10-30 words, in the sermon's own voice.
  Each slide has to earn the next swipe: end on tension or an open loop where natural.
- The last slide lands the takeaway in one or two short sentences, then a gentle one-line
  call to action (save it, share it, send it to someone who needs it) — never salesy.

WRITING RULES:
- Slovak, the language of the sermon; wording a non-churchgoer understands, no insider jargon
- short sentences, no filler — every word on a slide earns its place
- felt-need topics travel furthest: identity, anxiety, relationships, purpose, failure, hope
- quote the sermon's own striking phrases verbatim where they are strong
- carousels get saved and shared, not just seen: write lines people want to keep

For each carousel also write:
- caption: 1-3 Slovak sentences for the text under the post, ending with a question that
  invites comments; no hashtags in it
- hashtags: 5-8 lowercase hashtags without the # sign, mixing Slovak and broad-reach tags
- save_score (1-100): would a stranger save or share this? Be discriminating — use the full
  scale, spread the scores across the set, reserve 90+ for exceptional
- score_reason: one short Slovak sentence naming the carousel's strongest and weakest aspect

Order the carousels strongest first.

TRANSCRIPT:
{transcript}
"""


def select_carousels(
    segments: list[dict],
    count: int = 6,
    frames: int = 8,
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
    prompt = PROMPT_TEMPLATE.format(count=count, frames=frames, transcript=transcript)

    client = genai.Client()
    response = client.models.generate_content(
        model=gemini_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=CarouselList,
        ),
    )
    parsed = response.parsed
    if not isinstance(parsed, CarouselList):
        raise SystemExit(f"Gemini returned an unparseable response:\n{response.text}")

    finalized = [_finalize(c, frames) for c in parsed.carousels]
    finalized.sort(key=lambda c: c["save_score"], reverse=True)
    return finalized


def _finalize(c: Carousel, frames: int) -> dict:
    """Normalize one carousel: exact frame count, clean hashtags, parsed timestamps."""
    # the frame count is a promise to the user — cut what runs over; a short set is
    # kept as-is, since inventing a slide here would put words in the speaker's mouth
    slides = [s.strip() for s in c.slides if s.strip()][:frames]
    hashtags = []
    for tag in c.hashtags:
        tag = tag.strip().lstrip("#").lower().replace(" ", "")
        if tag and tag not in hashtags:
            hashtags.append(tag)
    start = parse_timestamp(c.source_start)
    end = max(parse_timestamp(c.source_end), start)
    return {
        "title": c.title,
        "source_start": format_timestamp(start),
        "source_end": format_timestamp(end),
        "source_start_sec": start,
        "source_end_sec": end,
        "slides": slides,
        "caption": c.caption.strip(),
        "hashtags": hashtags,
        "save_score": max(1, min(100, c.save_score)),
        "score_reason": c.score_reason,
    }


def write_carousels(
    carousels: list[dict],
    video_name: str,
    stem: str,
    out_dir: Path,
    gemini_model: str,
    frames: int,
) -> dict[str, Path]:
    """Write <stem>.carousels.json and <stem>.carousels.md; return the paths."""
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / f"{stem}.carousels.json"
    json_path.write_text(
        json.dumps(
            {
                "video": video_name,
                "gemini_model": gemini_model,
                "frames_per_carousel": frames,
                "carousels": carousels,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    lines = [f"# Carousels — {video_name}", ""]
    for i, c in enumerate(carousels, 1):
        lines += [
            f"## {i}. {c['title']}",
            "",
            f"**Zdroj:** {c['source_start']} → {c['source_end']}",
            "",
            f"**Skóre:** {c['save_score']}/100 — {c['score_reason']}",
            "",
        ]
        lines += [f"{n}. {slide}" for n, slide in enumerate(c["slides"], 1)]
        lines += [
            "",
            f"**Caption:** {c['caption']}",
            "",
            f"**Hashtagy:** {' '.join('#' + tag for tag in c['hashtags'])}",
            "",
        ]
    md_path = out_dir / f"{stem}.carousels.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")

    return {"json": json_path, "markdown": md_path}
