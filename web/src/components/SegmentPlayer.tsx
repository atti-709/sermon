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
 *  hook/full play buttons that stop at their range end. */
export const SegmentPlayer: React.FC<{
  videoUrl: string;
  startSec: number;
  endSec: number;
  hookStartSec?: number | null;
  hookEndSec?: number | null;
}> = ({ videoUrl, startSec, endSec, hookStartSec, hookEndSec }) => {
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
      <div className="row" style={{ gap: 6, marginTop: 6 }}>
        {hookStartSec != null && hookEndSec != null && (
          <button onClick={() => playRange(hookStartSec, hookEndSec)}>
            ▶ hook · {Math.round(hookEndSec - hookStartSec)}s
          </button>
        )}
        <button onClick={() => playRange(startSec, endSec)}>
          ▶ full · {fmtTime(endSec - startSec)}
        </button>
      </div>
    </div>
  );
};
