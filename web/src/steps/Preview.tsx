import { Player, PlayerRef } from "@remotion/player";
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CaptionedClipCore } from "@captions/CaptionedClipCore";
import { api } from "../api";
import type { Caption, ClipState, Framing, ProjectState } from "../types";

const FPS = 30;

/** index of the word being spoken at `tMs`, or -1 in the gaps between words */
const activeCaptionIndex = (captions: Caption[], tMs: number): number => {
  let lo = 0;
  let hi = captions.length - 1;
  let started = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (captions[mid].startMs <= tMs) {
      started = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return started !== -1 && tMs < captions[started].endMs + 150 ? started : -1;
};

/** Full transcript with every word editable — click a word to fix it (the
 *  player seeks along), Enter saves, Esc cancels. Memoized on the active word
 *  rather than the playhead: re-reconciling every word span 30x a second is
 *  enough main-thread work to stutter playback. */
const CaptionEditor = memo(function CaptionEditor({
  captions,
  activeIndex,
  onSeek,
  onSave,
}: {
  captions: Caption[];
  activeIndex: number;
  onSeek: (ms: number) => void;
  onSave: (updated: Caption[]) => void;
}) {
  const [editing, setEditing] = useState<number | null>(null);

  const commit = (index: number, value: string) => {
    setEditing(null);
    const trimmed = value.trim();
    if (!trimmed || trimmed === captions[index].text.trim()) return;
    const leadingSpace = captions[index].text.startsWith(" ") ? " " : "";
    onSave(captions.map((c, i) => (i === index ? { ...c, text: leadingSpace + trimmed } : c)));
  };

  return (
    <div className="caption-editor">
      {captions.map((caption, index) => {
        if (editing === index) {
          return (
            <input
              key={`edit-${index}`}
              autoFocus
              defaultValue={caption.text.trim()}
              style={{ width: `${Math.max(caption.text.trim().length + 2, 5)}ch` }}
              onKeyDown={(e) => {
                if (e.key === "Enter") commit(index, (e.target as HTMLInputElement).value);
                else if (e.key === "Escape") setEditing(null);
              }}
              onBlur={(e) => commit(index, e.target.value)}
            />
          );
        }
        const active = index === activeIndex;
        return (
          <span
            key={index}
            className={`ce-word${active ? " active" : ""}`}
            onClick={() => {
              onSeek(caption.startMs);
              setEditing(index);
            }}
          >
            {caption.text.trim()}{" "}
          </span>
        );
      })}
    </div>
  );
});

export const Preview: React.FC<{
  project: ProjectState;
  clip: ClipState;
  onNext: () => void;
}> = ({ project, clip, onNext }) => {
  const [captions, setCaptions] = useState<Caption[] | null>(null);
  const [framing, setFraming] = useState<Framing | null>(null);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [error, setError] = useState<string | null>(null);
  const [activeIndex, setActiveIndex] = useState(-1);
  const playerRef = useRef<PlayerRef>(null);
  const saveTimer = useRef<number | null>(null);

  useEffect(() => {
    setCaptions(null);
    setFraming(null);
    api
      .getCaptions(project.id, clip.id)
      .then(setCaptions)
      .catch((exc) => setError(exc.message));
    api
      .getFraming(project.id, clip.id)
      .then(setFraming)
      .catch(() => setFraming(null)); // framing is optional — centered crop without it
  }, [project.id, clip.id]);

  // follow playback so the transcript highlights the spoken word. `frameupdate`
  // fires every frame; storing the word index instead of the playhead keeps this
  // to ~3 re-renders a second (React bails out when the index is unchanged).
  useEffect(() => {
    const player = playerRef.current;
    if (!player || !captions) return;
    const onFrame = (e: { detail: { frame: number } }) =>
      setActiveIndex(activeCaptionIndex(captions, (e.detail.frame / FPS) * 1000));
    player.addEventListener("frameupdate", onFrame);
    return () => player.removeEventListener("frameupdate", onFrame);
  }, [captions]);

  const save = useCallback(
    (updated: Caption[]) => {
      setCaptions(updated);
      setSaveState("saving");
      api
        .putCaptions(project.id, clip.id, updated)
        .then(() => {
          setSaveState("saved");
          if (saveTimer.current) window.clearTimeout(saveTimer.current);
          saveTimer.current = window.setTimeout(() => setSaveState("idle"), 2000);
        })
        .catch((exc) => {
          setSaveState("error");
          setError(exc.message);
        });
    },
    [project.id, clip.id],
  );

  const seekToMs = useCallback(
    (ms: number) => playerRef.current?.seekTo(Math.round((ms / 1000) * FPS)),
    [],
  );

  // a fresh object here would re-render the whole composition on every parent
  // render — including once per frame while the transcript follows playback
  const inputProps = useMemo(
    () => ({
      videoSrc: clip.urls.video ?? "",
      captions,
      framing,
      fontUrl: "/media/app/fonts/Aspekta-600.ttf",
      editable: true,
      onSaveCorrections: save,
    }),
    [clip.urls.video, captions, framing, save],
  );

  if (error)
    return (
      <>
        <h2>Preview & Edit</h2>
        <p className="error">{error}</p>
      </>
    );
  if (!captions || !clip.urls.video || clip.duration_sec == null)
    return (
      <>
        <h2>Preview & Edit</h2>
        <p className="hint">Loading captions…</p>
      </>
    );

  return (
    <>
      <h2>Preview & Edit</h2>
      <p className="hint">
        This is the exact composition the final render uses. <strong>Click any word in the
        transcript</strong> to fix it — the player follows along; Enter saves, Esc cancels. Words in
        the video itself are clickable too.
      </p>
      <div className="row" style={{ alignItems: "flex-start", marginTop: 14, gap: 20 }}>
        <Player
          ref={playerRef}
          component={CaptionedClipCore}
          durationInFrames={Math.max(1, Math.ceil(clip.duration_sec * FPS))}
          fps={FPS}
          compositionWidth={1080}
          compositionHeight={1920}
          inputProps={inputProps}
          controls
          style={{ width: 330, borderRadius: 12, overflow: "hidden", flexShrink: 0 }}
        />
        <div style={{ flex: 1, minWidth: 280 }}>
          <p className="hint" style={{ marginTop: 0 }}>
            {captions.length} words ·{" "}
            {framing
              ? `speaker tracking active (${framing.keyframes.length} keyframes)`
              : "centered crop (no tracking data)"}
            <span style={{ minHeight: 20, marginLeft: 10 }}>
              {saveState === "saving" && "saving…"}
              {saveState === "saved" && "✓ saved (Studio & render will use it)"}
              {saveState === "error" && <span className="error">save failed</span>}
            </span>
          </p>
          <CaptionEditor
            captions={captions}
            activeIndex={activeIndex}
            onSeek={seekToMs}
            onSave={save}
          />
          <div className="row" style={{ marginTop: 14, justifyContent: "flex-end" }}>
            <button className="primary" onClick={onNext}>
              Next: Render →
            </button>
          </div>
        </div>
      </div>
    </>
  );
};
