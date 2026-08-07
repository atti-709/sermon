"""Who is talking, out of the faces in a landscape frame — and when the camera should cut.

The tracker in ../track.py follows one subject, chosen by face size or by hand. That is the
wrong shape for a two-hander or a panel: the person who should own the 9:16 frame is whoever
is *speaking*, and that changes several times inside a clip. Nothing in the transcript says
who, so this module reads it off the picture and the sound together:

- **Mouth movement.** Vision's face landmarks give the inner-lip contour per face; its
  vertical aperture, normalized to the face's own box, is the mouth opening. How much that
  aperture *changes* over a window is how much the mouth is moving.
- **Voiced audio.** A band-passed RMS envelope of the soundtrack says when anybody is
  speaking at all.

Mouth movement is only counted on samples where the audio is voiced, which is the whole
correlation in one line: a listener who nods, smiles or laughs in a pause scores nothing,
while the talker's movement lands exactly on the speech. The loudest mouth wins the window,
and hysteresis keeps the frame: a challenger has to lead for a moment before taking it, and
no speaker loses it before a minimum dwell. That turns interjections and back-channel "mhm"s
into nothing at all, instead of a camera that flickers between two people.

Turns become **hard cuts**, never pans: panning across two metres of stage to the other
speaker is exactly the shot a human operator would never make. ../track.py already treats a
cut as a segment boundary and starts the next segment where the new subject is, so the
timeline this module returns is fed in as cuts and the camera steps.
"""

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# Tunables (units: fraction of frame width, normalized lip aperture, seconds)
MATCH_RADIUS = 0.05  # a face this close to a track's last position is the same person
TRACK_LOST_SEC = 3.0  # a track nothing matched for this long is closed. Generous, because
# a person on a stage does not leave their spot: they turn their head away, put a hand over
# their mouth, look down at a note, and come back where they were. Cheap to keep waiting,
# and every premature close is a fresh track — another chance to "switch speaker" to
# somebody who never moved. The radius still keeps a reframing from continuing a track.
MIN_TRACK_SEC = 0.8  # shorter tracks are detector noise, not people
ACTIVITY_WIN_SEC = 1.5  # mouth movement is measured over this window, centered on the
# sample — offline there is no reason to trail the way a live operator has to. Long enough
# to ride out the pauses between phrases, which is when a listener's smile can win a
# shorter window outright.
MIN_VOICED_SAMPLES = 3  # a window with less speech than this judges nobody
VOICE_FLOOR_FRAC = 0.18  # voiced = envelope above this fraction of the clip's loud level
LOUD_PERCENTILE = 90  # ...which is this percentile of the envelope
MIN_MOUTH_ACTIVITY = 0.005  # aperture change per sample below this is a still mouth. Stays
# low on purpose: how big the number gets depends on how many pixels of lip the shot has,
# and a speaker in a wide two-shot measures barely above a listener in a close one. The
# margin below, which compares two mouths in the *same* frame, is the real test.
ACTIVITY_MARGIN = 2.2  # a challenger must beat the current speaker by this much...
SWITCH_CONFIRM_SEC = 0.9  # ...for this long, before the frame changes hands
MIN_DWELL_SEC = 2.0  # and no shot is shorter than this, whatever the mouths do
AUDIO_RATE = 16000  # the envelope only needs speech band, so 16 kHz mono is plenty
# ---------------------------------------------------------------------------


@dataclass
class Face:
    """One face in one sampled frame, in normalized frame coordinates."""

    cx: float
    cy: float
    confidence: float
    # vertical inner-lip aperture, as a fraction of the face's own box: comparable
    # between a face near the camera and one across the stage. None if landmarks failed.
    openness: float | None


@dataclass
class Track:
    """One person, followed across samples by position."""

    id: int
    t: list[float] = field(default_factory=list)
    cx: list[float] = field(default_factory=list)
    openness: list[float | None] = field(default_factory=list)

    @property
    def last_cx(self) -> float:
        return self.cx[-1]

    @property
    def last_t(self) -> float:
        return self.t[-1]


@dataclass
class Turn:
    """One stretch of the clip that belongs to one speaker.

    `score` and `rival` are the mouth activity of the winner and of whoever had the frame
    when it changed hands — the evidence for this turn, logged so a wrong cut can be read
    off the job output instead of guessed at."""

    start: float
    end: float
    track: int
    score: float = 0.0
    rival: float = 0.0


# ---------------------------------------------------------------------------
# Audio


def audio_envelope(video: Path, start: float | None, duration: float | None,
                   sample_hz: float, n_samples: int) -> np.ndarray | None:
    """Speech-band RMS of the soundtrack, one value per video sample.

    None when the file carries no audio — the caller then judges mouths alone."""
    seek = ["-ss", f"{start:.3f}"] if start else []
    dur = ["-t", f"{duration:.3f}"] if duration else []
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", *seek, "-i", str(video), *dur, "-vn",
         # the speech band only: stage rumble and hall reverb are not somebody talking
         "-af", "highpass=f=180,lowpass=f=3800",
         "-ac", "1", "-ar", str(AUDIO_RATE), "-f", "f32le", "-"],
        capture_output=True, check=False,
    )
    if proc.returncode != 0 or not proc.stdout:
        return None
    pcm = np.frombuffer(proc.stdout, dtype=np.float32)
    hop = AUDIO_RATE / sample_hz
    envelope = np.zeros(n_samples)
    for i in range(n_samples):
        chunk = pcm[int(i * hop):int((i + 1) * hop)]
        if len(chunk):
            envelope[i] = float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2)))
    return envelope


def voiced_mask(envelope: np.ndarray | None, n_samples: int) -> np.ndarray:
    """Which samples have somebody speaking. All of them when there is no audio."""
    if envelope is None or not envelope.any():
        return np.ones(n_samples, dtype=bool)
    loud = float(np.percentile(envelope, LOUD_PERCENTILE))
    return envelope > VOICE_FLOOR_FRAC * loud


# ---------------------------------------------------------------------------
# Faces into people


def build_tracks(frames: list[list[Face]], sample_hz: float) -> list[Track]:
    """Group per-frame faces into one track per person, by position.

    Greedy nearest-neighbour: the closest pairings inside `MATCH_RADIUS` win, unmatched
    faces open a track of their own. Enough for people sitting or standing on a stage,
    which is the only footage that gets here."""
    tracks: list[Track] = []
    live: list[Track] = []
    next_id = 0

    for index, faces in enumerate(frames):
        t = index / sample_hz
        live = [tr for tr in live if t - tr.last_t <= TRACK_LOST_SEC]
        pairs = sorted(
            ((abs(f.cx - tr.last_cx), fi, ti) for fi, f in enumerate(faces)
             for ti, tr in enumerate(live) if abs(f.cx - tr.last_cx) <= MATCH_RADIUS),
        )
        taken_faces: set[int] = set()
        taken_tracks: set[int] = set()
        for _, fi, ti in pairs:
            if fi in taken_faces or ti in taken_tracks:
                continue
            taken_faces.add(fi)
            taken_tracks.add(ti)
            face, track = faces[fi], live[ti]
            track.t.append(t)
            track.cx.append(face.cx)
            track.openness.append(face.openness)
        for fi, face in enumerate(faces):
            if fi in taken_faces:
                continue
            track = Track(id=next_id)
            next_id += 1
            track.t.append(t)
            track.cx.append(face.cx)
            track.openness.append(face.openness)
            tracks.append(track)
            live.append(track)

    return [tr for tr in tracks if len(tr.t) >= MIN_TRACK_SEC * sample_hz]


def _activity(track: Track, n_samples: int, sample_hz: float) -> np.ndarray:
    """Per-sample |change in mouth aperture|, NaN where the person was not seen.

    Only measured between consecutive observations: across a gap the mouth may have
    done anything, and calling that movement would credit a re-detection as speech."""
    out = np.full(n_samples, np.nan)
    step = 1.0 / sample_hz
    for i in range(1, len(track.t)):
        prev, now = track.openness[i - 1], track.openness[i]
        if prev is None or now is None or track.t[i] - track.t[i - 1] > 1.5 * step:
            continue
        index = int(round(track.t[i] * sample_hz))
        if 0 <= index < n_samples:
            out[index] = abs(now - prev)
    return out


def _positions(track: Track, n_samples: int, sample_hz: float) -> np.ndarray:
    """The track's x at every sample: interpolated over gaps, held flat past the ends."""
    grid = np.arange(n_samples) / sample_hz
    return np.interp(grid, np.array(track.t), np.array(track.cx))


# ---------------------------------------------------------------------------
# Turn taking


def speaker_timeline(tracks: list[Track], envelope: np.ndarray | None, n_samples: int,
                     sample_hz: float) -> list[Turn]:
    """Split the clip into turns: which track owns the frame, from when to when.

    The window is centered — this runs offline, so unlike a live operator it can see the
    speech starting and cut on it rather than after it."""
    if not tracks:
        return []
    duration = n_samples / sample_hz
    if len(tracks) == 1:
        return [Turn(0.0, duration, tracks[0].id)]

    voiced = voiced_mask(envelope, n_samples)
    activity = {tr.id: _activity(tr, n_samples, sample_hz) for tr in tracks}
    half = max(1, int(round(ACTIVITY_WIN_SEC * sample_hz / 2)))

    def window_scores(i: int) -> dict[int, float]:
        """Each face's mouth movement around sample `i`, counted only where somebody is
        audibly speaking. That condition is the whole audio-visual correlation: a listener
        who nods or laughs in a pause contributes nothing, because the pause is not voiced."""
        lo, hi = max(0, i - half), min(n_samples, i + half + 1)
        window_voiced = voiced[lo:hi]
        if int(window_voiced.sum()) < MIN_VOICED_SAMPLES:
            return {}
        scores = {}
        for track_id, series in activity.items():
            samples = series[lo:hi][window_voiced]
            samples = samples[~np.isnan(samples)]
            if len(samples) >= MIN_VOICED_SAMPLES:
                scores[track_id] = float(np.mean(samples))
        return scores

    turns: list[Turn] = []
    current: int | None = None
    turn_start, turn_score, turn_rival = 0.0, 0.0, 0.0
    challenger: int | None = None
    challenger_since = 0.0

    for i in range(n_samples):
        t = i / sample_hz
        scores = window_scores(i)
        if not scores:
            continue
        best_id, best = max(scores.items(), key=lambda item: item[1])
        if best < MIN_MOUTH_ACTIVITY:
            challenger = None  # nobody's mouth is really moving: no evidence either way
            continue
        if current is None:
            current, turn_start, turn_score = best_id, 0.0, best
            challenger = None
            continue
        held = scores.get(current, 0.0)
        if best_id == current or best < ACTIVITY_MARGIN * held:
            # either the frame is already on the moving mouth, or the challenger is not
            # clearly ahead of it — and an unclear case is not worth a cut
            challenger = None
            continue
        if challenger != best_id:
            challenger, challenger_since = best_id, t
            continue
        if t - challenger_since >= SWITCH_CONFIRM_SEC and t - turn_start >= MIN_DWELL_SEC:
            # the cut lands where the challenger started talking, not where the evidence
            # finished accumulating — offline, there is no reason to cut late
            cut_at = max(challenger_since, turn_start + MIN_DWELL_SEC)
            turns.append(Turn(turn_start, cut_at, current, turn_score, turn_rival))
            current, turn_start, challenger = best_id, cut_at, None
            turn_score, turn_rival = best, held

    if current is not None:
        turns.append(Turn(turn_start, duration, current, turn_score, turn_rival))
    return turns


def subject_path(turns: list[Turn], tracks: list[Track], t_grid: np.ndarray,
                 n_samples: int, sample_hz: float,
                 min_jump: float = 0.0) -> tuple[np.ndarray, list[float]]:
    """The subject position over time, stepping at each turn, plus the cut times.

    Inside a turn the path follows that speaker's own movement, so the camera still
    holds and pans over them the way it does for a single subject.

    A turn boundary only becomes a cut when the frame actually has to move `min_jump`
    to reach the new speaker. A face that was lost for a moment — head turned away,
    hand over the mouth — comes back as a new track, and handing the frame from one
    fragment of a person to the next must not cut to where the camera already is."""
    if not turns:
        return np.full_like(t_grid, 0.5), []
    by_id = {tr.id: _positions(tr, n_samples, sample_hz) for tr in tracks}
    subject = np.full_like(t_grid, 0.5)
    for turn in turns:
        span = (t_grid >= turn.start) & (t_grid < turn.end)
        if not span.any():
            continue
        positions = by_id[turn.track]
        indices = np.clip(np.round(t_grid[span] * sample_hz).astype(int), 0, n_samples - 1)
        subject[span] = positions[indices]

    cuts = []
    for turn in turns[1:]:
        i = int(np.searchsorted(t_grid, turn.start))
        if 0 < i < len(subject) and abs(subject[i] - subject[i - 1]) > min_jump:
            cuts.append(turn.start)
    return subject, cuts
