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
- **Carousels** turn the same transcript into Instagram carousel posts: you choose how many
  carousels and how many frames each, Gemini writes the slide texts (cover line, one thought
  per frame, closing takeaway) plus a caption and hashtags, ready to paste into the design
  template. Saved as `.carousels.json`/`.carousels.md` in `00_SOURCE/`.
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
word to fix a typo, or clear it and press Enter to delete an extra word the transcriber
invented (the surrounding words keep their timings); edits land in the same
`.captions.json` Studio and the render read.

## Where files go

Each highlight you export gets a folder of its own, named after the highlight and
numbered by its virality rank, with one folder per stage inside it. The sermon's own
folder keeps just the source video and those highlight folders:

```
Movies/kazen/
  _SPEAKERS.txt                      who preaches here (optional, see Intro graphics)
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

### MKV recordings (and other containers)

OBS records to `.mkv` by default, and Matroska is the one container nothing at the
far end of this pipeline opens: neither Chrome — which draws every preview and does
the decoding for the final render — nor DaVinci Resolve, which links the file the
Resolve XML names. ffmpeg has no such problem, so transcription, speaker tracking
and the vertical export read a `.mkv` directly and nothing about them changes.

For the two that can't, the Highlights step offers **Make a playable copy**, which
writes `00_SOURCE/<stem>.mp4` — the browser preview and the Resolve timeline then
use that. It is a **remux, not a transcode**: an OBS recording is already h264/aac,
so the streams are copied into the new container untouched. That runs at disk speed
(a 45-minute sermon in a few seconds), the copy is the same size as the source, and
no frame is re-encoded, so the timeline still cuts on exactly the frames you
previewed. Your recording is never modified or moved.

A real re-encode happens only when the video codec itself is undecodable — a ProRes
or DNxHD master, an FFV1 recording — because then there is nothing to copy; it goes
to hardware h264 at 30 Mbps. PCM audio is converted to AAC on the way in (seconds,
and the video copy stays untouched) since an MP4 cannot carry it. The same rule
applies to rendered clips on their way into `captions/public/`, so a clip handed to
the captions step in any container gets a copy Chrome can actually decode.

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
SIL Open Font License 1.1 (`OFL.txt` sits beside it). To rebrand, drop your own files in that
folder and update both references to them — `FONT_FILES` in `captions/src/CaptionedClip.tsx`
(Studio and renders) and `FONT_URLS` in `web/src/steps/Preview.tsx` (the web preview);
a system stack takes over whenever a file is missing.

## Intro graphics (speaker name + logo)

Typing a speaker name on the Preview step turns on the branded intro, a port of the
CAMPFEST DaVinci speaker-clips template: the bottom of the frame progressively blurs
and darkens, the name (Aspekta 700) fades in over it and hands over to the CAMPFEST
pill logo at ~4.6 s; the logo and the bottom treatment then stay up for the rest of
the clip. The name is saved per clip as `speakerName` in the `.style.json` sidecar —
the web preview, Remotion Studio and the final render all read the same file, and an
empty name means no intro at all. Geometry and timing live at the top of
`captions/src/CaptionedClipCore.tsx` (`INTRO_*` constants, measured off the designer's
finished clip; blur and vignette strengths calibrated against a DaVinci render of the
actual template); the logo is `captions/public/intro/campfest-border.png` — swap that
file to rebrand.

### Names pre-filled from the programme

A sermon folder can say who preaches in it, so the field arrives filled in instead of
being retyped off a run sheet with the diacritics guessed. Put a `_SPEAKERS.txt` next
to the source video — one name per line, `#` for comments:

```
# Speaker names for this sermon — sobota 8.8. · HANGÁR
#   20:15  evanjel. 25'+výzva 10'  ·  Michal Irsák - Pravda o Ježišovi
#   22:25  recap téma + final výzva  ·  Miro Tóth - Odvaha žiť v pravde
Michal Irsák
Miro Tóth
```

The first name pre-fills the intro card of every clip cut from that sermon; the rest
appear as buttons under the field, for a folder with more than one preacher in it (an
evening programme has the evangelist and the recap). The pre-fill only applies until
the clip has a `.style.json` of its own — after that the clip keeps whatever is in the
field, an empty name included, and nothing writes back to `_SPEAKERS.txt`.

For CampFest, `scripts/speaker_names.py` fills these in from the programme
spreadsheet across a whole festival tree:

```bash
uv run scripts/speaker_names.py \
    --schedule "~/Movies/Campfest2026/SCHEDULE/CF 2026 - PROGRAM.xlsx" \
    ~/Movies/Campfest2026            # --dry-run to look first, --force to replace
```

It reads every venue's columns off each day's sheet, keeps the rows that are somebody
preaching (`Seminár`, `téma`, `evanjelizácia`, `recap téma`), works out which side of
`Meno - Názov prednášky` is the person, and matches each one to its folder under
`CF/<day>/VIDEOS/04_SERMONS/`. Folders are never created and existing files are kept
unless `--force` — a name corrected by hand has to survive a re-run.

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

### More than one person in frame

A panel or an interview breaks the "biggest face is the speaker" guess: people side by side
score alike, so the camera locks onto whoever the detector happened to like in the first frame
and stays there while the others talk off-screen. Three buttons under a highlight's preview on
the Highlights step say who the crop belongs to, and the answer is saved into the highlights
file, so re-exports, the Resolve XML and the notes all follow it.

**Biggest face** is the default and the whole of the old behaviour.

**One person** — click them in the preview. The 9:16 window the export would cut is drawn over
the picture as you aim (`subject_x`, normalized x). The pick seeds the track and keeps it on a
short leash: a detection more than ~0.12 of the frame width from the running track is a
different person, even when it is the only face on screen, which is what stops the track
hopping to the neighbour during a blind stretch. A click within ~0.08 of a real face snaps to
it; a pick nobody matches holds a still crop right there. Both segments of a hook-first export
share the one pick — tracked separately, they could otherwise each lock onto a different
panelist.

**Whoever speaks** (`follow_speaker`) works out who is talking and **cuts** between them — see
`src/sermon/speakers.py`. Vision's face landmarks give each mouth's inner-lip aperture as a
fraction of its own face box, so two people's mouths are comparable however far away they sit;
how much that aperture moves over a window is how much the mouth is moving. A band-passed RMS
envelope of the soundtrack says when anybody is speaking, and **mouth movement is only counted
on voiced samples** — that condition is the entire audio-visual correlation in one line, since a
listener who nods, smiles or laughs in a pause contributes nothing. On real two-hander footage
the talker measures ~10× the listener (0.03 against 0.003).

Turns become hard cuts, never pans — travelling two metres across a stage is the shot no
operator would make — and the existing cut machinery does the rest: a cut is already a segment
boundary the solver starts afresh at, so the camera lands on the new speaker in a single frame.
What keeps that from flickering:

- A challenger must beat **the current speaker** by 2.2× for 0.9 s before the frame changes
  hands, and no shot is shorter than 2 s. Weak interjections and back-channel "mhm"s come to
  nothing; a strong one (~5× the held speaker) earns its two-second shot.
- *Whether* to cut and *where* are decided separately. The scores' own boundary is smeared by
  the measuring window (centered, so it leads the truth by up to half a window), so each
  confirmed cut is placed on the incoming speaker's **first observed mouth movement** — on the
  breath before their line when there is one, and **never before their face has been seen**:
  its position before that is extrapolation, and the source may still be mid-transition to the
  shot that contains it. In practice the line's first syllable may lead the picture by a
  beat — a J-cut, which is what a human editor would do anyway.
- A vanished owner (walked off, source changed shots) is not a speaker holding their turn:
  the successor is confirmed on a startle reflex instead of the full 0.9 s.
- A turn only becomes a cut if the frame actually has to **move** ~0.4 of a crop width. A face
  lost for a moment (head turned away, hand over the mouth) comes back as a fresh track, and
  handing the shot from one fragment of a person to the next must not cut to where the camera
  already is.
- A hand-picked subject wins over speaker cutting: it is the more specific instruction.

The source is often a **switcher feed that changes camera angles mid-clip**, and the analysis
is built around that. A scene-score spike counts as a real shot change only when the face
*population* moves across it — the LED wall changing slides spikes the score without moving
anybody, and this check sees the whole frame where the single-subject check (used by the
non-speaker modes) sees only its own track. A confirmed shot change then does three things:

- **orphans every track** — position means nothing across a camera change, and a track that
  straddled one would register the reframe itself as mouth movement, planting a false
  "speaking" spike exactly where a wrong decision hurts most;
- **becomes the cut point** for any switch confirmed near it: the crop's jump hides inside the
  source's own cut, where a viewer sees one cut instead of two a beat apart — and it always
  re-seats the crop, so even a same-speaker reframe lands as a clean step on the source's cut
  rather than a pan right after it;
- **frees the frame**: if it took the current speaker off screen entirely (a reaction shot),
  the crop falls back to whoever is most reliably on camera, talking or not — holding a
  position carried over from the previous shot's geometry frames nobody at all. This fallback
  fires *only* when a shot change actually removed the owner; a speaker whose face is briefly
  lost mid-shot keeps their frame.

Also available from the CLI on an already-rendered clip: `uv run sermon track clip.mp4
--follow-speaker`. Judging mouths costs a wider decode (1440 px) and the landmark detector,
about 10 ms per sampled frame — a 6-minute stretch takes ~35 s.
