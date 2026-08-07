"""Where every file the pipeline produces goes.

One folder per highlight, named after the highlight and numbered by its virality
rank, with one folder per stage inside it. The sermon's own folder keeps only the
source video and those highlight folders; everything the pipeline derives from
the video as a whole sits in `00_SOURCE/`:

    <sermon folder>/
        _SPEAKERS.txt                      who preaches here, off the festival programme
        kazen.mp4                          the source video, untouched
        00_SOURCE/                         transcript, segments, highlights, Resolve XML
        01_Boh ťa vidí/
            01_VERTICAL_NO_CAPTION/        the tracked 9:16 export, straight out of ffmpeg
            02_DAVINCI_EXPORT/             where the edit rendered out of DaVinci is saved
            03_CAPTIONING/                 caption/framing/style JSON — metadata only, no video
            Boh ťa vidí.mp4                the finished, captioned video

The rank prefix keeps Finder's order the same as the highlights list, and the
finished video is named after its highlight alone, since its folder already says
which sermon it came from.

A clip's stage folder is what locates its metadata: `sidecar()` resolves a JSON
path from the clip's own position on disk, so no step needs to be told where the
previous one put things.
"""

import hashlib
import re
from pathlib import Path

SOURCE_DIR = "00_SOURCE"
VERTICAL_DIR = "01_VERTICAL_NO_CAPTION"
DAVINCI_DIR = "02_DAVINCI_EXPORT"
CAPTIONING_DIR = "03_CAPTIONING"

# who preaches in this folder, taken off the festival programme (see
# scripts/speaker_names.py). The one file here nothing in the pipeline writes.
SPEAKERS_FILE = "_SPEAKERS.txt"

STAGE_DIRS = (VERTICAL_DIR, DAVINCI_DIR, CAPTIONING_DIR)
CLIP_DIRS = (VERTICAL_DIR, DAVINCI_DIR)

# a highlight folder: two-digit rank, underscore, the highlight's title
HIGHLIGHT_DIR_RE = re.compile(r"^(\d{2})_(.+)$")

# characters no filesystem (or Finder) should have to take in a name
_ILLEGAL_RE = re.compile(r'[/\\:*?"<>|]|[\x00-\x1f]')
MAX_TITLE_LEN = 64


def safe_name(title: str) -> str:
    """A Gemini title as a folder name: the same words, minus what a path cannot hold.

    Diacritics stay — the sermons are Slovak and the folders are read by humans."""
    name = " ".join(_ILLEGAL_RE.sub("", title).split())
    return name[:MAX_TITLE_LEN].strip(" .") or "highlight"


def ensure_parent(path: Path) -> Path:
    """Create the folder a file is about to be written into. Returns the file path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


# --- project level -----------------------------------------------------------


def source_dir(video: Path) -> Path:
    return video.resolve().parent / SOURCE_DIR


def source_file(video: Path, suffix: str) -> Path:
    """A whole-sermon artifact: `<video stem>.<suffix>` inside `00_SOURCE/`."""
    video = video.resolve()
    return source_dir(video) / f"{video.stem}.{suffix}"


def video_for_source_file(artifact: Path, video_name: str) -> Path:
    """The source video an artifact in `00_SOURCE/` belongs to."""
    folder = artifact.resolve().parent
    return (folder.parent if folder.name == SOURCE_DIR else folder) / video_name


def speakers_file(sermon_dir: Path) -> Path:
    """The sermon folder's speaker names, pre-filled from the festival programme.

    Takes the folder, not the video, because it is the one artifact that predates
    the footage: the programme is known months ahead, so the file can be written
    into an empty folder and be waiting there when the card is copied off. It sits
    at the top of the sermon folder rather than in `00_SOURCE/` for the same
    reason — the people who correct it read it in Finder, not through the app."""
    return sermon_dir / SPEAKERS_FILE


# --- highlight level ---------------------------------------------------------


def highlight_dir_name(index: int, title: str) -> str:
    return f"{index:02d}_{safe_name(title)}"


def highlight_dir(video: Path, index: int, title: str | None = None) -> Path | None:
    """The folder for highlight `index` (1-based, as ranked in the highlights file).

    An existing `NN_*` folder wins over the title: re-running the highlights step
    rewords titles, and an export already sitting on disk must not be orphaned by
    a rename. Returns None when nothing exists yet and no title was given."""
    parent = video.resolve().parent
    existing = sorted(
        p for p in parent.glob(f"{index:02d}_*") if p.is_dir() and p.name != SOURCE_DIR
    )
    if existing:
        return existing[0]
    return parent / highlight_dir_name(index, title) if title else None


def highlight_title(highlight: Path) -> str:
    """The highlight's name, without the rank prefix its folder carries."""
    match = HIGHLIGHT_DIR_RE.match(highlight.name)
    return match.group(2) if match else highlight.name


def vertical_dir(highlight: Path) -> Path:
    return highlight / VERTICAL_DIR


def davinci_dir(highlight: Path) -> Path:
    return highlight / DAVINCI_DIR


def captioning_dir(highlight: Path) -> Path:
    return highlight / CAPTIONING_DIR


def ensure_highlight_dirs(highlight: Path) -> Path:
    """Create the highlight folder and all three stage folders.

    All three, even though only the first is written now: `02_DAVINCI_EXPORT` is
    where the user saves their edit from DaVinci, so it has to exist for them to
    save into."""
    for name in STAGE_DIRS:
        (highlight / name).mkdir(parents=True, exist_ok=True)
    return highlight


def vertical_clip(highlight: Path) -> Path:
    """The tracked 9:16 export for this highlight."""
    return vertical_dir(highlight) / f"{highlight_title(highlight)}.mov"


def final_render(highlight: Path) -> Path:
    """The finished, captioned video — the one file in the highlight's own folder."""
    return highlight / f"{highlight_title(highlight)}.mp4"


# --- clip metadata -----------------------------------------------------------


def highlight_dir_for_clip(clip: Path) -> Path | None:
    """The highlight a clip belongs to, read off its position on disk."""
    folder = clip.resolve().parent
    if folder.name in CLIP_DIRS:
        return folder.parent
    if HIGHLIGHT_DIR_RE.match(folder.name) and (folder / CAPTIONING_DIR).is_dir():
        return folder
    return None


def sermon_dir_for_clip(clip: Path) -> Path | None:
    """The sermon folder a clip came from — its highlight's parent.

    None for a clip registered from outside the layout, which belongs to no sermon
    folder and so has no programme to read a speaker name off."""
    highlight = highlight_dir_for_clip(clip)
    return highlight.parent if highlight is not None else None


def sidecar_dir(clip: Path) -> Path:
    """Where this clip's JSON metadata lives.

    Its highlight's `03_CAPTIONING/`, or one beside the clip when it sits outside
    the layout entirely — a clip may be registered from anywhere on disk."""
    highlight = highlight_dir_for_clip(clip)
    if highlight is not None:
        return captioning_dir(highlight)
    return clip.resolve().parent / CAPTIONING_DIR


def sidecar(clip: Path, suffix: str) -> Path:
    """A metadata file for `clip`: captions.json, framing.json, style.json, …

    The stage number is kept in the name because both clips of one highlight are
    routinely called the same thing — DaVinci names its render after the timeline,
    which is named after the highlight, which is what the vertical export is
    called. Without the prefix they would share one set of captions."""
    folder = clip.resolve().parent.name
    stem = f"{folder[:2]}_{clip.stem}" if folder in CLIP_DIRS else clip.stem
    return sidecar_dir(clip) / f"{stem}.{suffix}"


# --- the Remotion app's public/ folder ---------------------------------------


def public_name(clip: Path) -> str:
    """Name for this clip's copy inside `captions/public/`.

    Remotion resolves media through `staticFile()`, so that folder is flat while
    the clips it holds are not: same-named clips from two highlights (or from the
    two stages of one highlight) would overwrite each other and then read each
    other's captions. The digest of the clip's folder keeps them apart."""
    digest = hashlib.sha1(str(clip.resolve().parent).encode("utf-8")).hexdigest()[:8]
    return f"{digest}_{clip.name}"


def public_sidecar_name(clip: Path, suffix: str) -> str:
    """Matching name for a sidecar in `captions/public/` — the composition derives
    it from the video's own name by swapping the extension."""
    return f"{Path(public_name(clip)).stem}.{suffix}"
