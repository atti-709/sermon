import { Caption } from "@remotion/captions";
import { loadFont } from "@remotion/fonts";
import { Video } from "@remotion/media";
import { useEffect, useMemo, useState } from "react";
import {
  AbsoluteFill,
  OffthreadVideo,
  continueRender,
  delayRender,
  interpolate,
  spring,
  useCurrentFrame,
  useRemotionEnvironment,
  useVideoConfig,
} from "remotion";
import { buildPages, LINE_LEAD_MS } from "./layout";

// Pure composition core: no staticFile(), no @remotion/studio — the caller
// (Remotion Studio wrapper or the sermon web app's <Player>) supplies full
// media URLs and a save callback, so the same pixels render everywhere.
// Page/line-breaking rules live in layout.ts.

// ---------------------------------------------------------------------------
// Hard-coded caption style — tweak here, not via props.
const FONT_FAMILY = "Aspekta";
const FONT_FALLBACK = "'Arial Black', 'Helvetica Neue', sans-serif";
const FONT_WEIGHT = 600;
const FONT_SIZE = 64;
const TEXT_TRANSFORM: React.CSSProperties["textTransform"] = "none";
const TEXT_COLOR = "white";
const STROKE = "12px black";
const BOTTOM_OFFSET = 560; // px from the bottom of the 1920px frame — keeps captions
// above the Reels/TikTok/Shorts UI cluster (bottom ~25% of the screen)
const LINE_POP_MS = 200; // entry animation length per line
const DIMMED_LINE_OPACITY = 0.6; // a line fades to this once a later line starts
// ---------------------------------------------------------------------------

// `sermon track` output: where the 9:16 crop window should sit inside the source
// video over time (cx = crop-center X as a fraction of source width). Between
// keyframes the path is linearly interpolated; it is emitted densely enough that
// this reproduces the smoothed camera move. Without a framing file the crop
// stays centered, exactly as before.
export type Framing = {
  sourceWidth: number;
  sourceHeight: number;
  keyframes: { t: number; cx: number }[];
};

const framingCxAt = (keyframes: Framing["keyframes"], tSec: number): number => {
  if (keyframes.length === 0) return 0.5;
  if (tSec <= keyframes[0].t) return keyframes[0].cx;
  const last = keyframes[keyframes.length - 1];
  if (tSec >= last.t) return last.cx;
  let lo = 0;
  let hi = keyframes.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (keyframes[mid].t <= tSec) lo = mid;
    else hi = mid;
  }
  const a = keyframes[lo];
  const b = keyframes[hi];
  return a.cx + ((b.cx - a.cx) * (tSec - a.t)) / (b.t - a.t || 1);
};

// Frame source: @remotion/media's <Video> while rendering — it decodes with
// Mediabunny/WebCodecs (hardware decode) instead of spawning ffmpeg per frame — and
// <OffthreadVideo> in Studio and the web app's <Player>, where a plain <video> element
// scrubs and plays back better. Both elements carry the source's intrinsic size, so the
// same `object-fit: cover` + `object-position` crop applies to either; <Video> takes
// objectFit as a prop (it paints into a <canvas>) and warns if it arrives via style.
const ClipVideo: React.FC<{ src: string; objectPosition: string }> = ({ src, objectPosition }) => {
  const { isRendering } = useRemotionEnvironment();
  const style = { width: "100%", height: "100%", objectPosition };
  return isRendering && process.env.REMOTION_FORCE_OFFTHREAD !== "1" ? (
    <Video src={src} objectFit="cover" style={style} />
  ) : (
    <OffthreadVideo src={src} style={{ ...style, objectFit: "cover" }} />
  );
};

export const CaptionedClipCore: React.FC<{
  /** full URL of the clip (staticFile(...) in Studio, /media/app/... in the web app) */
  videoSrc: string;
  captions: Caption[] | null;
  framing?: Framing | null;
  /** full URL of the brand font; falls back to a system stack when missing */
  fontUrl?: string | null;
  /** allow click-to-edit of caption words */
  editable?: boolean;
  /** Studio only: claim canvas clicks via window capture listeners (Studio's
   *  selection tool swallows React synthetic events) */
  captureClicksForStudio?: boolean;
  /** receives the full corrected caption array after each word edit */
  onSaveCorrections?: (updated: Caption[]) => void;
}> = ({
  videoSrc,
  captions: captionsFromProps,
  framing,
  fontUrl,
  editable = false,
  captureClicksForStudio = false,
  onSaveCorrections,
}) => {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const timeMs = (frame / fps) * 1000;

  // corrections show up immediately via this local copy; the caller persists
  // them in the background through onSaveCorrections
  const [edited, setEdited] = useState<{ src: string; captions: Caption[] } | null>(null);
  const captions = edited?.src === videoSrc ? edited.captions : captionsFromProps;

  const [fontHandle] = useState(() => delayRender("loading brand font"));
  useEffect(() => {
    if (!fontUrl) {
      continueRender(fontHandle);
      return;
    }
    // loadFont() cancels the render outright on a missing file, so probe first
    fetch(fontUrl, { method: "HEAD" })
      .then((r) =>
        r.ok
          ? loadFont({ family: FONT_FAMILY, url: fontUrl })
          : console.warn(`${fontUrl} not found — using fallback font`),
      )
      .catch(() => console.warn(`${fontUrl} not reachable — using fallback font`))
      .finally(() => continueRender(fontHandle));
  }, [fontHandle, fontUrl]);

  const pages = useMemo(() => (captions ? buildPages(captions) : []), [captions]);

  // word currently being edited, identified by its start timestamp
  const [editing, setEditing] = useState<{ fromMs: number; text: string } | null>(null);

  // Studio's selection tool captures canvas clicks via React synthetic events,
  // so word spans never receive them. Native capture-phase listeners on window
  // run before any React handler: when the press lands on a caption word we
  // claim it for editing; everything else (timeline, scrubbing) is untouched.
  useEffect(() => {
    if (!editable || !captureClicksForStudio) return;
    const claim = (e: MouseEvent | PointerEvent) => {
      const word = document
        .elementsFromPoint(e.clientX, e.clientY)
        .find((el): el is HTMLElement => el instanceof HTMLElement && el.dataset.wordFrom !== undefined);
      if (!word) return;
      e.stopImmediatePropagation();
      e.preventDefault();
      if (e.type === "pointerdown") {
        setEditing({ fromMs: Number(word.dataset.wordFrom), text: word.dataset.wordText ?? "" });
      }
    };
    const opts = { capture: true } as const;
    window.addEventListener("pointerdown", claim, opts);
    window.addEventListener("mousedown", claim, opts);
    window.addEventListener("click", claim, opts);
    return () => {
      window.removeEventListener("pointerdown", claim, opts);
      window.removeEventListener("mousedown", claim, opts);
      window.removeEventListener("click", claim, opts);
    };
  }, [editable, captureClicksForStudio]);

  const saveCorrection = (fromMs: number, corrected: string) => {
    setEditing(null);
    if (!captions || corrected.trim() === "") return;
    const index = captions.findIndex((c) => c.startMs === fromMs);
    if (index === -1 || corrected.trim() === captions[index].text.trim()) return;
    const leadingSpace = captions[index].text.startsWith(" ") ? " " : "";
    const updated = captions.map((c, i) =>
      i === index ? { ...c, text: leadingSpace + corrected.trim() } : c,
    );
    setEdited({ src: videoSrc, captions: updated });
    onSaveCorrections?.(updated);
  };

  const page = pages.find((p) => timeMs >= p.startMs && timeMs < p.endMs);

  // subject tracking: slide the cover-cropped video so the crop window follows
  // the speaker (see `sermon track`); px offset of the scaled video's left edge
  let objectPosition = "center";
  if (framing && framing.keyframes.length > 0) {
    const displayedWidth = height * (framing.sourceWidth / framing.sourceHeight);
    if (displayedWidth > width + 0.5) {
      const cx = framingCxAt(framing.keyframes, timeMs / 1000);
      const left = Math.min(0, Math.max(width - displayedWidth, width / 2 - cx * displayedWidth));
      objectPosition = `${left}px 50%`;
    }
  }

  return (
    <AbsoluteFill style={{ backgroundColor: "black" }} showInTimeline={false}>
      <ClipVideo src={videoSrc} objectPosition={objectPosition} />
      {page ? (
        <AbsoluteFill style={{ justifyContent: "flex-end", alignItems: "center" }}>
          <div
            style={{
              marginBottom: BOTTOM_OFFSET,
              width: "84%",
              textAlign: "center",
              fontFamily: `${FONT_FAMILY}, ${FONT_FALLBACK}`,
              fontSize: FONT_SIZE,
              fontWeight: FONT_WEIGHT,
              lineHeight: 1.2,
              textTransform: TEXT_TRANSFORM,
              color: TEXT_COLOR,
            }}
          >
            {page.lines.map((line, lineIndex) => {
              // whole-line reveal: a line pops in as one unit slightly before
              // its first word is spoken (reading outruns listening); once a
              // later line starts, this one de-emphasizes
              const lineStart = Math.max(0, line[0].fromMs - LINE_LEAD_MS);
              const started = timeMs >= lineStart;
              const laterLineStarted = page.lines.some(
                (other, otherIndex) =>
                  otherIndex > lineIndex && timeMs >= Math.max(0, other[0].fromMs - LINE_LEAD_MS),
              );
              const progress = spring({
                frame,
                fps,
                delay: (lineStart / 1000) * fps,
                durationInFrames: (LINE_POP_MS / 1000) * fps,
                config: { damping: 200 },
              });
              // while editing, not-yet-spoken lines stay faintly visible so
              // their words can be clicked and corrected; renders keep them hidden
              const ghost = editable && !started;
              return (
                <div
                  key={lineIndex}
                  style={{
                    visibility: started || ghost ? "visible" : "hidden",
                    opacity: ghost ? 0.3 : progress * (laterLineStarted ? DIMMED_LINE_OPACITY : 1),
                    transform: ghost
                      ? undefined
                      : `translateY(${interpolate(progress, [0, 1], [14, 0])}px)`,
                  }}
                >
                  {line.map((token, i) => {
                    if (editable && editing?.fromMs === token.fromMs) {
                      return (
                        <input
                          key={i}
                          autoFocus
                          defaultValue={token.text.trim()}
                          onClick={(e) => e.stopPropagation()}
                          onKeyDown={(e) => {
                            e.stopPropagation();
                            if (e.key === "Enter") {
                              saveCorrection(token.fromMs, (e.target as HTMLInputElement).value);
                            } else if (e.key === "Escape") {
                              setEditing(null);
                            }
                          }}
                          onBlur={(e) => saveCorrection(token.fromMs, e.target.value)}
                          style={{
                            fontFamily: "inherit",
                            fontWeight: "inherit",
                            fontSize: FONT_SIZE * 0.8,
                            width: `${Math.max(token.text.trim().length + 2, 6)}ch`,
                            textAlign: "center",
                            borderRadius: 12,
                            border: "4px solid #4290f5",
                            outline: "none",
                          }}
                        />
                      );
                    }
                    return (
                      <span
                        key={i}
                        data-word-from={editable ? token.fromMs : undefined}
                        data-word-text={editable ? token.text : undefined}
                        onPointerDown={
                          editable && !captureClicksForStudio
                            ? (e) => {
                                e.stopPropagation();
                                setEditing({ fromMs: token.fromMs, text: token.text });
                              }
                            : undefined
                        }
                        style={{
                          whiteSpace: "pre",
                          WebkitTextStroke: STROKE,
                          paintOrder: "stroke fill",
                          textShadow: "0 4px 16px rgba(0,0,0,0.45)",
                          cursor: editable ? "pointer" : undefined,
                        }}
                      >
                        {token.text + (i < line.length - 1 ? " " : "")}
                      </span>
                    );
                  })}
                </div>
              );
            })}
          </div>
        </AbsoluteFill>
      ) : null}
    </AbsoluteFill>
  );
};
