import { Player, PlayerRef } from "@remotion/player";
import { useEffect, useMemo, useRef } from "react";
import { AbsoluteFill, OffthreadVideo, useVideoConfig } from "remotion";
import { fmtTime } from "../api";

const FPS = 30;

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
 *  shrinks the preview with it. */
export const SegmentPlayer: React.FC<{
  videoUrl: string;
  startSec: number;
  endSec: number;
  hookStartSec?: number | null;
  hookEndSec?: number | null;
  /** receives an absolute source timestamp when the ✂ button is pressed */
  onSetEnd?: (sec: number) => void;
}> = ({ videoUrl, startSec, endSec, hookStartSec, hookEndSec, onSetEnd }) => {
  const ref = useRef<PlayerRef>(null);
  const stopFrame = useRef<number | null>(null);
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

  const playRange = (fromSec: number, toSec: number) => {
    const player = ref.current;
    if (!player) return;
    stopFrame.current = Math.round((toSec - startSec) * FPS);
    player.seekTo(Math.max(0, Math.round((fromSec - startSec) * FPS)));
    player.play();
  };

  const inputProps = useMemo(() => ({ src: videoUrl, fromSec: startSec }), [videoUrl, startSec]);

  return (
    <div className="hl-preview">
      <Player
        ref={ref}
        component={Segment}
        durationInFrames={durationInFrames}
        fps={FPS}
        compositionWidth={1920}
        compositionHeight={1080}
        inputProps={inputProps}
        controls
        style={{ width: 340, borderRadius: 8, overflow: "hidden" }}
      />
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
    </div>
  );
};
