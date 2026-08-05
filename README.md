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
- **Vertical export** cuts each highlight *before* the edit: the speaker is tracked with the
  Apple Vision framework and the 9:16 crop follows them, so DaVinci receives a 1080×1920
  ProRes and your B-roll, titles and trims land in the final vertical frame. This lives on
  the Highlights step of the web UI; the CLI still offers the older horizontal route.

## Setup

Requires **macOS on Apple Silicon**: transcription uses the Metal GPU, speaker tracking the
Neural Engine, rendering the VideoToolbox encoder. You also need [Homebrew](https://brew.sh)
and git — plus DaVinci Resolve (the free edition is enough) for the editing step.

```sh
git clone https://github.com/atti-709/sermon.git
cd sermon
./scripts/setup.sh
```

The script installs ffmpeg, node and [uv](https://docs.astral.sh/uv/) via Homebrew if they're
missing, then the Python dependencies, the Remotion renderer in `captions/`, and the web UI in
`web/` (which it also builds — without that build, `sermon web` serves the API only).

Then put a Gemini API key in the `.env` the script created at the repo root:

```sh
GEMINI_API_KEY=...   # free key: https://aistudio.google.com/apikey
```

It's needed for highlight selection and the caption proofread; everything else runs offline.
Keys are free and per-person, so get your own rather than sharing one.

**Updating** — `./scripts/update.sh` pulls the latest commit and re-runs the setup, which is
idempotent and takes seconds when the dependencies haven't moved. The Python code runs
straight from the checkout, so most updates need nothing beyond the pull.

Budget ~2 GB of disk for the dependencies (`uv sync` pulls torch for WhisperX) and another
~2 GB for models, which download on demand the first time you use each step: whisper
`large-v3-turbo` and a WhisperX alignment model for word timings (both cached in
`~/.cache/huggingface`), plus a headless Chrome the first time Remotion renders. After that
only the Gemini calls touch the network.

Run everything from the checkout with `uv run sermon …`. A global `uv tool install .` covers
`transcribe`/`highlights`/`export` only — the captions, tracking and web steps resolve
`captions/` and `web/dist` relative to the repo.

## Web UI (the easy way)

A local web app guides you through the whole pipeline — pick video → transcribe →
highlights, each exportable as a tracked vertical clip → Resolve hand-off → captions →
preview with click-to-edit words → final render:

```sh
uv run sermon web   # opens http://127.0.0.1:8756
```

The server runs on your Mac, so everything Apple-native still applies: transcription
on the Metal GPU, face tracking on the Neural Engine, "Reveal in Finder" buttons.
Long steps run as subprocesses with live logs and progress streamed to the browser;
project state is derived from the folders next to your video (see **Where files go**
below), so you can quit and resume anytime — recent projects are remembered in
`~/.sermon/recents.json`. **Cancel** stops a step for real: each job runs in its own
process group and cancelling takes down every process it started — the ffmpeg behind
an export, the node and headless Chrome behind a render — and deletes the half-written
file it was producing.

The preview step embeds the exact Remotion composition the final render uses
(`captions/src/CaptionedClipCore.tsx`, shared with Remotion Studio) — click a caption
word to fix a typo; edits land in the same `.captions.json` Studio and the render read.

## Where files go

Each highlight you export gets a folder of its own, named after the highlight and
numbered by its virality rank, with one folder per stage inside it. The sermon's own
folder keeps just the source video and those highlight folders:

```
Movies/kazen/
  kazen.mp4                          the source video, untouched
  00_SOURCE/                         transcript, segments, highlights, Resolve XML, project sidecar
  01_Boh ťa vidí/
      01_VERTICAL_NO_CAPTION/        the tracked 9:16 export, straight out of ffmpeg
      02_DAVINCI_EXPORT/             you render your DaVinci edit into here
      03_CAPTIONING/                 caption / framing / style / cuts JSON — metadata only
      Boh ťa vidí.mp4                the finished, captioned video
  02_Nikdy nie si sám/
      …
```

The finished video is named after its highlight alone — its folder already says which
sermon and which moment. A clip's stage folder is what locates its metadata, so no step
has to be told where the previous one put things (`src/sermon/layout.py` owns all of it).

The Render step's **Change…** button points a project at one folder for every render
instead (an export drive, a Dropbox folder); the choice is remembered in
`00_SOURCE/<stem>.project.json` and **Use the highlight's folder** clears it again.

For frontend development: `uv run sermon web --no-browser` in one terminal,
`npm run dev` in `web/` in another (Vite proxies `/api` and `/media`).

## Usage (CLI)

```sh
# everything in one go: transcript + highlights + Resolve timeline
uv run sermon run kazen.mp4

# or step by step
uv run sermon transcribe kazen.mp4
uv run sermon highlights kazen.mp4       # reuses kazen.segments.json
uv run sermon export kazen.mp4           # re-generate just the Resolve XML
```

Outputs land in the video's `00_SOURCE/` folder (override with `-o`):

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

This is the horizontal route: you edit in 16:9 and the 9:16 crop happens afterwards, at the
captions step. The web UI's vertical export inverts that — you edit clips that are already
cropped and tracked — and is the better default. It has no CLI equivalent yet.

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
#   + speaker tracking for the 9:16 crop (see below; --no-track to skip) — skipped
#     automatically when the clip is already vertical, i.e. came from a vertical export
# fix any remaining typos in Studio (click a word) or in rendered_clip.captions.json
cd captions && npm run studio              # preview; renders via the Studio render button, or:
npx remotion render CaptionedClip --props='{"src":"rendered_clip.mp4","captions":null}' out/final.mp4
```

Renders lean on Apple Silicon at both ends: VideoToolbox h264 encoding (`remotion.config.ts`,
~8 Mbps — hardware encoders ignore crf, so quality is pinned by bitrate) and WebCodecs
decoding via `<Video>` from `@remotion/media`, which replaces `<OffthreadVideo>`'s
ffmpeg-per-frame extraction while rendering. Together they take a 90-second clip from ~110s to
~62s, and the render time stops fluctuating run to run. Studio and the web app's `<Player>`
keep `<OffthreadVideo>`, which scrubs better; the two agree on framing and colour, and differ
only in which of two adjacent source frames a 50 → 30 fps output frame samples (20 ms).

`sermon captions` writes `<clip>.captions.json` (one entry per word) into the clip's
`03_CAPTIONING/` folder and copies the clip plus its JSON into `captions/public/`, where
the names carry a short digest of the clip's folder — that folder is flat, and two
highlights are routinely called the same thing. The caption style is hard-coded in
`captions/src/CaptionedClipCore.tsx`: 9:16 vertical, karaoke pages of ~3-5 words where words
appear progressively as spoken (the full page stays visible once complete). Captions are set
in Aspekta 600 by Ivo Dolenc, bundled at `captions/public/fonts/Aspekta-600.ttf` under the
SIL Open Font License 1.1 (`OFL.txt` sits beside it). To rebrand, drop your own file in that
folder and update both references to it — `FONT_FILE` in `captions/src/CaptionedClip.tsx`
(Studio and renders) and the `fontUrl` in `web/src/steps/Preview.tsx` (the web preview);
a system stack takes over whenever the file is missing.

## Speaker tracking (the 9:16 crop follows the preacher)

One solver, two places to apply it. The **vertical export** (web UI, before the edit) runs it
over the highlight's time window and bakes the moving crop into the exported clip — a single
ffmpeg pass where a `perspective` filter slides the fractional crop quad with per-frame
expressions (subpixel positions at the full frame rate; an integer `sendcmd` crop was visibly
steppy), scaled to 1080×1920. The **horizontal route** (after the edit) instead keeps the clip
in 16:9 and writes a `framing.json` sidecar, smoothed X offsets that the Remotion app applies per
frame, so the crop moves at render time:

```sh
uv run sermon track rendered_clip.mp4      # ~10 s per clip; runs automatically in `sermon captions`
uv run sermon track rendered_clip.mp4 --debug   # + preview video with the crop window drawn in
```

Either way the camera behaves the same:

- Face detection runs on the **Apple Neural Engine** (Vision framework, ~7 ms/frame,
  10 samples/s), with a human-body fallback when the face is turned away.
- The virtual camera behaves like a calm operator, not like OpusClip's jitter: it **holds
  still** (~80 % of the time) while the speaker stays inside a dead zone, ignores brief
  excursions that return within a few seconds, and otherwise **pans once, smoothly**
  (minimum-jerk, ≈1-3 s) to where the speaker is about to settle — and it never whips back
  mid-pan.
- Pans **react, they don't anticipate**: a pan only launches after the drift has been
  visible for a human beat (~0.35 s; ~0.15 s startle-reflex when the speaker is striding
  out fast, judged from observed speed, never from future frames). Look-ahead is used only
  to choose where the pan lands, so the camera trails the way a real operator does.
- Hard cuts (e.g. the hook) are detected via ffmpeg scene scores but only honored when the
  subject position actually jumps across them — the camera then snaps exactly at the cut
  instead of gliding. LED-wall slide changes behind the speaker are ignored.
- No faces at all → the crop stays centered, same as before tracking existed.
