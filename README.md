# sermon

Transcribe Slovak sermon videos locally and pick social-media highlight moments with Gemini.

- **Transcription** runs fully on-device with [mlx-whisper](https://pypi.org/project/mlx-whisper/)
  (Apple's MLX framework — uses the Mac's GPU via Metal). Default model: `large-v3-turbo`.
- **Highlight selection** sends the timestamped transcript (text only, no audio/video) to the
  Gemini API, which suggests clip candidates with Slovak titles, summaries, a 3–10 s **hook**
  moment per clip (the final video opens with the hook, then plays the full clip), and a
  **virality score** (1–100, a hook-weighted composite of hook/flow/value/reach sub-scores,
  OpusClip-style) for fast triage. Shorter is better: the prompt targets 30–90 s and
  hard-caps at 110 s. You fine-tune the exact cut points yourself.

## Setup

Requires macOS on Apple Silicon, [uv](https://docs.astral.sh/uv/), and ffmpeg (`brew install ffmpeg`).

```sh
uv sync
export GEMINI_API_KEY=...   # https://aistudio.google.com/apikey — only needed for highlights
```

Optionally install it as a global command: `uv tool install .`

## Usage

```sh
# everything in one go: transcript + highlights + Resolve timeline
uv run sermon run kazen.mp4

# or step by step
uv run sermon transcribe kazen.mp4
uv run sermon highlights kazen.mp4       # reuses kazen.segments.json
uv run sermon export kazen.mp4           # re-generate just the Resolve XML
```

Outputs land next to the video (override with `-o`):

| File | Contents |
| --- | --- |
| `kazen.transcript.txt` | one `[HH:MM:SS] text` line per segment — easiest to skim |
| `kazen.srt` | standard subtitles |
| `kazen.segments.json` | machine-readable segments (input for the highlights step) |
| `kazen.highlights.md` | suggested clips: time range, hook range + text, virality score, Slovak title, summary, excerpt |
| `kazen.highlights.json` | the same, machine-readable (`start_sec`/`end_sec`/`hook_*` for cutting) |
| `kazen.resolve.xml` | FCP7 XML timeline for DaVinci Resolve — hook clip + full clip per highlight, back-to-back |

Import in DaVinci Resolve via **File → Import Timeline → Import AAF, EDL, XML…**.
The timeline references the original video (nothing is re-encoded), each clip is named
after its highlight, and since the full source is linked you can roll each clip's
edges to fine-tune the cut points.

`sermon run` skips transcription when a `.segments.json` already exists (`--force` to redo),
so re-running highlights with different settings is cheap.

## Options

```sh
sermon run kazen.mp4 \
  -m large-v3           # better Slovak accuracy, ~4x slower than turbo (default: large-v3-turbo)
  -n 10                 # how many highlights to request (default: 8)
  --min-duration 20 --max-duration 110 \
  --gemini-model gemini-3.5-flash   # default: gemini-flash-latest
  -l sk                 # spoken language (default: sk)
```

Model shortcuts: `tiny`, `small`, `medium`, `large-v3`, `large-v3-turbo`/`turbo`,
or any Hugging Face repo id (e.g. `mlx-community/whisper-large-v3-mlx`).
Models download once (~1.6 GB for turbo) and are cached in `~/.cache/huggingface`.

Rough speed on an M3 Pro with `large-v3-turbo`: a 45-minute sermon transcribes in a few minutes.

## Captions (after the DaVinci edit)

Once you've cut the clip in Resolve (B-roll, trims) and rendered it, generate word-level
captions and burn them in with the Remotion app in `captions/`:

```sh
uv run sermon captions rendered_clip.mp4   # WhisperX word-level timestamps (CPU, ~1 min/clip)
#   + automatic Gemini proofread: fixes Czech spillover, diacritics, mishearings —
#     never paraphrases (word-for-word repairs only; --no-grammar-check to skip)
# fix any remaining typos in Studio (click a word) or in rendered_clip.captions.json
cd captions && npm run studio              # preview; renders via the Studio render button, or:
npx remotion render CaptionedClip --props='{"src":"rendered_clip.mp4","captions":null}' out/final.mp4
```

`sermon captions` writes `<clip>.captions.json` (one entry per word) next to the clip and
copies both into `captions/public/`. The caption style is hard-coded in
`captions/src/CaptionedClip.tsx`: 9:16 vertical, karaoke pages of ~3-5 words where words
appear progressively as spoken (the full page stays visible once complete). Drop your brand
font at `captions/public/fonts/brand.otf`; a fallback stack is used until then.
