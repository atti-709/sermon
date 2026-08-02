"""Word-level caption generation for rendered clips via WhisperX (transcribe + forced alignment)."""

import json
import re
import shutil
from pathlib import Path

from pydantic import BaseModel, Field

# Remotion captions app inside this repo; `sermon captions` copies the video + JSON here
APP_PUBLIC_DIR = Path(__file__).resolve().parents[2] / "captions" / "public"


# Hesitation sounds never belong in captions (clean-read style). Matched on the word
# with punctuation stripped and lowercased; a shape regex catches spelling variants
# (ééé, ehmmm). Deliberately conservative: at least 2 letters, and real Slovak words
# that double as fillers ("no", "tak", "aha", single-letter prepositions) never match.
FILLER_WORDS = frozenset({"ehm", "hm", "mhm", "uhm", "um", "uh", "eh"})
_HESITATION_RE = re.compile(r"^(?:[eé]{1,4}h?m{0,3}|h{1,2}m{1,3}|m{2,4}h?m{0,2}|u{1,3}h?m{0,2}|[aá]{2,4}h?)$")


def is_filler(word: str) -> bool:
    bare = word.strip().strip(".,!?…;:\"'").lower()
    return len(bare) >= 2 and (bare in FILLER_WORDS or bool(_HESITATION_RE.match(bare)))


def generate_captions(video: Path, model: str = "large-v3-turbo", language: str = "sk") -> list[dict]:
    """Run WhisperX and return Remotion `Caption[]`: one entry per word."""
    import whisperx

    device = "cpu"  # no CUDA on macOS; clips are short so CPU is fine
    audio = whisperx.load_audio(str(video))

    asr = whisperx.load_model(model, device, compute_type="int8", language=language)
    result = asr.transcribe(audio, language=language)

    align_model, metadata = whisperx.load_align_model(language_code=language, device=device)
    aligned = whisperx.align(result["segments"], align_model, metadata, audio, device)

    def _bare(text: str) -> str:
        return text.strip().strip(".,!?…;:\"'").lower()

    captions = []
    for segment in aligned["segments"]:
        for word in segment["words"]:
            if is_filler(word["word"]):
                continue
            if "start" not in word:  # words the aligner could not place (numbers, etc.)
                if captions:
                    captions[-1]["text"] += " " + word["word"].strip()
                continue
            # stutter repeats render once ("že že" → "že"); rhetorical repetition
            # is protected by its punctuation ("Svätý, svätý" keeps both)
            if (
                captions
                and _bare(word["word"]) == _bare(captions[-1]["text"])
                and not captions[-1]["text"].rstrip().endswith((",", ".", "!", "?", "…", ";", ":"))
            ):
                captions[-1]["endMs"] = round(word["end"] * 1000)
                continue
            start_ms = round(word["start"] * 1000)
            captions.append(
                {
                    "text": (" " if captions else "") + word["word"].strip(),
                    "startMs": start_ms,
                    "endMs": round(word["end"] * 1000),
                    "timestampMs": start_ms,
                    "confidence": word.get("score"),
                }
            )
    return captions


class WordCorrection(BaseModel):
    index: int = Field(description="Index of the word in the numbered list")
    corrected: str = Field(
        description="The corrected single word, keeping any attached punctuation. "
        "An empty string means: delete this word (hesitation fillers only)."
    )
    reason: str = Field(description="Very short tag: 'czech form', 'diacritics', 'mishearing', 'casing', 'filler', 'brand', …")


class CorrectionList(BaseModel):
    corrections: list[WordCorrection]


PROOFREAD_PROMPT = """\
You are proofreading an automatic speech-recognition transcript of a sermon spoken in SLOVAK.
It was produced by Whisper, which makes characteristic mistakes on Slovak speech:
- it slips into CZECH spellings and word forms (e.g. "protože" instead of "pretože", "kteří" \
instead of "ktorí"; the letters ě, ř, ů do not exist in Slovak at all)
- it drops or misplaces diacritics ("zamyslime" instead of "zamyslíme")
- it mishears a consonant or syllable, producing a non-word or a similar-sounding wrong word \
("hdovec" instead of "vdovec")
- it occasionally miscapitalizes proper nouns and sentence starts ("boh" → "Boh", "ježiš" → "Ježiš")
- it spells brand names and foreign proper nouns phonetically ("rejbeny" instead of "Ray-Bany",
  "fejsbuk" instead of "Facebook", "ajfon" instead of "iPhone")
- it sometimes transcribes hesitation sounds as words ("ehm", "hmm", "uhm")

Your job is ONLY to repair such transcription errors — you are restoring what the speaker
actually said, not editing their language.

STRICT RULES:
1. Never paraphrase, reorder, translate, or stylistically improve anything. The speaker's
   informal or colloquial phrasing stays exactly as spoken.
2. A correction must sound nearly identical to the original when read aloud — you are fixing
   the spelling of the SAME spoken word, never choosing a better word. For brand names and
   foreign proper nouns, use the official spelling while keeping the spoken Slovak
   inflection ending ("rejbenov" → "Ray-Banov").
3. Each correction replaces exactly one word with exactly one word. Never merge or split words.
   Exception: a hesitation filler (ehm, hm, uhm and similar non-words) is corrected to an
   EMPTY string, which removes it from the captions. Only pure hesitation sounds may be
   removed — real words ("no", "tak", "vlastne") always stay, even when used as fillers.
4. Keep punctuation attached to the word unless the word itself is wrong. When removing a
   filler that carried sentence punctuation ("Ehm,"), just remove it — do not move the
   punctuation elsewhere.
5. Grammatical errors made by the SPEAKER (spoken wrong endings, slang) must be kept.
6. If you are not confident the speaker actually said your corrected form, leave the word
   unchanged. Few safe corrections beat many risky ones.

Full transcript for context:
{text}

Numbered words (return corrections only for words that need fixing):
{numbered}
"""


def proofread_captions(captions: list[dict], gemini_model: str) -> list[tuple[int, str, str, str]]:
    """Ask Gemini to fix Whisper's Slovak transcription errors in place.
    Returns the applied corrections as (index, before, after, reason)."""
    from google import genai
    from google.genai import types

    words = [c["text"].strip() for c in captions]
    prompt = PROOFREAD_PROMPT.format(
        text=" ".join(words),
        numbered="\n".join(f"{i}: {w}" for i, w in enumerate(words)),
    )

    client = genai.Client()
    response = client.models.generate_content(
        model=gemini_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.0,
            response_mime_type="application/json",
            response_schema=CorrectionList,
        ),
    )
    parsed = response.parsed
    if not isinstance(parsed, CorrectionList):
        raise RuntimeError(f"unparseable Gemini response: {response.text[:200]}")

    applied = []
    deletions = []
    for corr in parsed.corrections:
        if not 0 <= corr.index < len(captions):
            continue
        before = captions[corr.index]["text"]
        after = corr.corrected.strip()
        if not after:
            # empty correction = delete (hesitation fillers only — be safe about it)
            if is_filler(before):
                deletions.append(corr.index)
                applied.append((corr.index, before.strip(), "", corr.reason))
            continue
        # one word must stay one word, or the per-word timings break
        if " " in after or after == before.strip():
            continue
        captions[corr.index]["text"] = (" " if before.startswith(" ") else "") + after
        applied.append((corr.index, before.strip(), after, corr.reason))
    # delete back-to-front so earlier indices stay valid
    for index in sorted(set(deletions), reverse=True):
        del captions[index]
    return applied


def write_captions(captions: list[dict], video: Path, copy_to_app: bool = True) -> dict[str, Path]:
    json_path = video.resolve().parent / f"{video.stem}.captions.json"
    json_path.write_text(json.dumps(captions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths = {"captions": json_path}

    if copy_to_app and APP_PUBLIC_DIR.is_dir():
        shutil.copy2(video, APP_PUBLIC_DIR / video.name)
        shutil.copy2(json_path, APP_PUBLIC_DIR / json_path.name)
        paths["app"] = APP_PUBLIC_DIR / video.name
    return paths
