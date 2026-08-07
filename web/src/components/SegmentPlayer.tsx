import { Player, PlayerRef } from "@remotion/player";
import { useEffect, useMemo, useRef, useState } from "react";
import { AbsoluteFill, OffthreadVideo, useVideoConfig } from "remotion";
import { fmtTime } from "../api";

const FPS = 30;
/** the preview composition, and the vertical clip the export cuts out of it */
const STAGE_ASPECT = 16 / 9;
const OUT_ASPECT = 9 / 16;

/** Plays one time window of a longer source video (horizontal, letterboxed). */
const Segment: React.FC<{ src: string; fromSec: number }> = ({ src, fromSec }) => {
  const { fps } = useVideoConfig();
  return (
    <AbsoluteFill style={{ backgroundColor: "black" }}>
      <OffthreadVideo
        src={src}
        startFrom={Math.round(fromSec * fps)}
        style={{ width: "100%", height: "100%", objectFit: "contain" }}
      />
    </AbsoluteFill>
  );
};

/** Remotion Player over a highlight's range of the source sermon, with
 *  hook/full play buttons that stop at their range end. The player is exactly
 *  as long as the clip that would be exported — moving the out point grows or
 *  shrinks the preview with it.
 *
 *  With `onPickSubject` it also frames: the 9:16 window the export would cut is
 *  drawn over the picture, and clicking a person moves it onto them. */
export const SegmentPlayer: React.FC<{
  videoUrl: string;
  startSec: number;
  endSec: number;
  hookStartSec?: number | null;
  hookEndSec?: number | null;
  /** receives an absolute source timestamp when the ✂ button is pressed */
  onSetEnd?: (sec: number) => void;
  /** width / height of the source frame; the crop window needs its real aspect */
  sourceAspect?: number | null;
  /** normalized x of the person the export follows, null when nobody was picked */
  subjectX?: number | null;
  /** null = back to letting the tracker choose the largest face */
  onPickSubject?: (x: number | null) => void;
}> = ({
  videoUrl,
  startSec,
  endSec,
  hookStartSec,
  hookEndSec,
  onSetEnd,
  sourceAspect,
  subjectX,
  onPickSubject,
}) => {
  const ref = useRef<PlayerRef>(null);
  const stopFrame = useRef<number | null>(null);
  const [picking, setPicking] = useState(false);
  const [hoverX, setHoverX] = useState<number | null>(null);
  const durationInFrames = Math.max(1, Math.ceil((endSec - startSec) * FPS));

  useEffect(() => {
    const player = ref.current;
    if (!player) return;
    const onFrame = (e: { detail: { frame: number } }) => {
      if (stopFrame.current != null && e.detail.frame >= stopFrame.current) {
        stopFrame.current = null;
        player.pause();
      }
    };
    player.addEventListener("frameupdate", onFrame);
    return () => player.removeEventListener("frameupdate", onFrame);
  }, []);

  useEffect(() => {
    if (!picking) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setPicking(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [picking]);

  const playRange = (fromSec: number, toSec: number) => {
    const player = ref.current;
    if (!player) return;
    stopFrame.current = Math.round((toSec - startSec) * FPS);
    player.seekTo(Math.max(0, Math.round((fromSec - startSec) * FPS)));
    player.play();
  };

  const inputProps = useMemo(() => ({ src: videoUrl, fromSec: startSec }), [videoUrl, startSec]);

  // a source that is not 16:9 sits letterboxed inside the composition, so both the
  // crop window and a click on it map through the displayed picture, not the element
  const aspect = sourceAspect && sourceAspect > 0 ? sourceAspect : STAGE_ASPECT;
  const boxWidth = aspect >= STAGE_ASPECT ? 1 : aspect / STAGE_ASPECT;
  const boxLeft = (1 - boxWidth) / 2;
  // the crop's width as a fraction of the source frame (0.316 for 16:9 → 9:16)
  const cropFraction = Math.min(1, OUT_ASPECT / aspect);
  const half = cropFraction / 2;

  const shownX = picking ? hoverX ?? subjectX : subjectX;
  const center = shownX == null ? null : Math.min(Math.max(shownX, half), 1 - half);

  const sourceXAt = (clientX: number, target: HTMLDivElement): number => {
    const rect = target.getBoundingClientRect();
    const x = ((clientX - rect.left) / rect.width - boxLeft) / boxWidth;
    return Math.min(Math.max(x, 0), 1);
  };

  return (
    <div className="hl-preview">
      <div className="hl-stage">
        <Player
          ref={ref}
          component={Segment}
          durationInFrames={durationInFrames}
          fps={FPS}
          compositionWidth={1920}
          compositionHeight={1080}
          inputProps={inputProps}
          controls
          style={{ width: "100%" }}
        />
        {onPickSubject && (center != null || picking) && (
          <div
            className={`crop-guide${picking ? " picking" : ""}`}
            onMouseMove={(e) => picking && setHoverX(sourceXAt(e.clientX, e.currentTarget))}
            onMouseLeave={() => setHoverX(null)}
            onClick={(e) => {
              if (!picking) return;
              onPickSubject(sourceXAt(e.clientX, e.currentTarget));
              setPicking(false);
              setHoverX(null);
            }}
          >
            {center != null && (
              <div
                className="crop-window"
                style={{
                  left: `${(boxLeft + (center - half) * boxWidth) * 100}%`,
                  width: `${cropFraction * boxWidth * 100}%`,
                }}
              />
            )}
          </div>
        )}
      </div>
      <div className="row" style={{ gap: 8, marginTop: 10 }}>
        {/* what to play, grouped as one control; trimming is a separate action */}
        <div className="seg" role="group" aria-label="Play a part of this highlight">
          {hookStartSec != null && hookEndSec != null && (
            <button className="sm" onClick={() => playRange(hookStartSec, hookEndSec)}>
              <span aria-hidden="true">▶</span> Hook · {Math.round(hookEndSec - hookStartSec)}s
            </button>
          )}
          <button className="sm" onClick={() => playRange(startSec, endSec)}>
            <span aria-hidden="true">▶</span> Full · {fmtTime(endSec - startSec)}
          </button>
          {onSetEnd && (
            <button className="sm" onClick={() => playRange(Math.max(startSec, endSec - 4), endSec)}>
              <span aria-hidden="true">▶</span> Ending
            </button>
          )}
        </div>
        {onSetEnd && (
          <button
            className="sm"
            title="End the clip at the current playhead"
            onClick={() => {
              const frame = ref.current?.getCurrentFrame();
              if (frame != null) onSetEnd(startSec + frame / FPS);
            }}
          >
            <span aria-hidden="true">✂</span> End here
          </button>
        )}
      </div>
      {onPickSubject && (
        <div className="row" style={{ gap: 8, marginTop: 8 }}>
          <button
            className={picking ? "sm primary" : "sm"}
            aria-pressed={picking}
            onClick={() => {
              setPicking(!picking);
              setHoverX(null);
            }}
          >
            {picking ? "Cancel" : subjectX == null ? "Follow one person" : "Move the crop"}
          </button>
          {subjectX != null && !picking && (
            <button className="sm" onClick={() => onPickSubject(null)}>
              Clear
            </button>
          )}
          <span className="hint" style={{ flex: 1, minWidth: 120, fontSize: 12 }}>
            {picking
              ? "Click the person the crop should follow — Esc to cancel."
              : subjectX == null
                ? "Several people in frame? Pick whose face the 9:16 crop keeps."
                : "The crop starts on the marked person and pans with them."}
          </span>
        </div>
      )}
    </div>
  );
};
