import { Caption } from "@remotion/captions";
import { loadFont } from "@remotion/fonts";
import { Video } from "@remotion/media";
import { useEffect, useMemo, useState } from "react";
import {
  AbsoluteFill,
  Img,
  OffthreadVideo,
  continueRender,
  delayRender,
  interpolate,
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
// the line being spoken carries the weight; lines already spoken stay fully
// opaque but drop to regular, so the eye lands on the current one without a
// fade doing the work
const FONT_WEIGHT_CURRENT = 600;
const FONT_WEIGHT_SPOKEN = 400;
const FONT_SIZE = 64;
const TEXT_TRANSFORM: React.CSSProperties["textTransform"] = "none";
const TEXT_COLOR = "white";
// No outline: a hard stroke reads as a sticker pasted over the speaker. Legibility
// comes from a soft drop shadow instead — a tight one for contrast against light
// clothing, a wide diffuse one that darkens the background behind the whole word.
const TEXT_SHADOW = "0 2px 6px rgba(0,0,0,0.42), 0 6px 28px rgba(0,0,0,0.5)";
const BOTTOM_OFFSET = 640; // px from the bottom of the 1920px frame — keeps captions
// above the Reels/TikTok/Shorts UI cluster (bottom ~25% of the screen)
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Intro graphics — a port of the DaVinci speaker-clips template
// (ASSETS/SpeakerClips-Template_V1/26-SpeakerClipsTemplateV1.drp): the bottom
// of the frame blurs and darkens, the speaker's name fades in over it, hands
// over to the CAMPFEST logo, and everything bows out before the 9-second mark.
// Geometry and timing are measured off the template's reference render
// (sermon_intro.mov, 25 fps), so the numbers below are in seconds and frame px.
const INTRO_NAME_IN = [0, 0.32]; // linear fades throughout, as in the reference
const INTRO_NAME_OUT = [4.28, 4.6];
const INTRO_LOGO_IN = [4.64, 5.0];
// no exit: in the template the blur, dim and logo span the whole timeline, so
// once the name has handed over to the logo they stay to the end of the clip
// Aspekta 700 at this size puts the name's ink exactly where the designer's
// finished clip (sample.mp4) has it — 107 px solved from the sample's ink
// width, baseline at y≈1465; the top pins the baseline via the font's own
// metrics (ascent 1100/1000 em, line-height 1 → baseline 0.885 em from top)
const INTRO_NAME_SIZE = 107;
const INTRO_NAME_TOP = 1370;
// the logo PNG's canvas is 1073×290 with the pill centered in it; 708 px wide
// centers the visible pill at (540, 1430), matching the reference
const INTRO_LOGO_WIDTH = 708;
const INTRO_LOGO_TOP = 1335;
// The template's adjustment clip runs a zoom blur anchored at the top of the
// frame with the middle excluded, so streaks build toward the bottom; stacked
// backdrop-filter layers with gradient masks give the same progressive blur.
// Strengths are calibrated against a render of the actual template (dot-grid
// footage through DaVinci): the blur only bites in the bottom quarter,
// reaching σ ≈ 10 px vertically / 2 px horizontally at the frame's edge —
// these isotropic radii track the geometric mean of that.
const INTRO_BLUR_LAYERS = [
  { radius: 2.5, top: 1400, solidFrom: 150 },
  { radius: 3, top: 1510, solidFrom: 150 },
  { radius: 3, top: 1640, solidFrom: 150 },
  { radius: 3, top: 1780, solidFrom: 140 },
];
// the template's vignette, measured off the same calibration render: a faint
// kiss at the very top and the bottom falling to half brightness at the edge
const INTRO_DIM_GRADIENT =
  "linear-gradient(to bottom, rgba(0,0,0,0.095) 0px, rgba(0,0,0,0.03) 150px, rgba(0,0,0,0) 400px, " +
  "rgba(0,0,0,0) 1100px, rgba(0,0,0,0.031) 1300px, rgba(0,0,0,0.092) 1500px, rgba(0,0,0,0.254) 1700px, " +
  "rgba(0,0,0,0.4) 1850px, rgba(0,0,0,0.5) 1920px)";
// ---------------------------------------------------------------------------

const fade = (tSec: number, [from, to]: number[]) =>
  interpolate(tSec, [from, to], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

const IntroGraphics: React.FC<{ tSec: number; speakerName: string; logoUrl?: string | null }> = ({
  tSec,
  speakerName,
  logoUrl,
}) => {
  const nameOpacity = fade(tSec, INTRO_NAME_IN) - fade(tSec, INTRO_NAME_OUT);
  const logoOpacity = fade(tSec, INTRO_LOGO_IN);
  return (
    <AbsoluteFill>
      {INTRO_BLUR_LAYERS.map(({ radius, top, solidFrom }) => (
        <div
          key={top}
          style={{
            position: "absolute",
            left: 0,
            right: 0,
            top,
            bottom: 0,
            backdropFilter: `blur(${radius}px)`,
            WebkitBackdropFilter: `blur(${radius}px)`,
            maskImage: `linear-gradient(to bottom, transparent 0, black ${solidFrom}px)`,
            WebkitMaskImage: `linear-gradient(to bottom, transparent 0, black ${solidFrom}px)`,
          }}
        />
      ))}
      <div style={{ position: "absolute", inset: 0, background: INTRO_DIM_GRADIENT }} />
      {nameOpacity > 0 ? (
        <div
          style={{
            position: "absolute",
            left: 0,
            right: 0,
            top: INTRO_NAME_TOP,
            textAlign: "center",
            fontFamily: `${FONT_FAMILY}, ${FONT_FALLBACK}`,
            fontWeight: 700,
            fontSize: INTRO_NAME_SIZE,
            lineHeight: 1,
            color: "white",
            textShadow: TEXT_SHADOW, // same soft legibility shadow as the captions
            opacity: nameOpacity,
          }}
        >
          {speakerName}
        </div>
      ) : null}
      {logoUrl && logoOpacity > 0 ? (
        <Img
          src={logoUrl}
          style={{
            position: "absolute",
            left: (1080 - INTRO_LOGO_WIDTH) / 2,
            top: INTRO_LOGO_TOP,
            width: INTRO_LOGO_WIDTH,
            opacity: logoOpacity,
            // the template gives the logo a centered soft shadow (drop shadow,
            // distance 0) so the white pill holds up over bright footage
            filter: "drop-shadow(0 0 14px rgba(0,0,0,0.73))",
          }}
        />
      ) : null}
    </AbsoluteFill>
  );
};

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
  return isRendering ? (
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
  /** px to lift the caption block above its default height (negative = lower);
   *  set per clip by the web app's caption-position slider */
  yOffset?: number | null;
  /** splice points in seconds (a hook-first export) — no caption page spans one */
  cuts?: number[] | null;
  /** who is speaking — turns on the intro graphics (blur, name, logo) */
  speakerName?: string | null;
  /** full URL of the intro logo (staticFile(...) in Studio, /media/app/... in the web app) */
  introLogoUrl?: string | null;
  /** full URL of each brand-font weight the style uses (700 for the intro
   *  name, 600 for the current line, 400 for spoken ones); falls back to a
   *  system stack when missing */
  fontUrls?: { weight: number; url: string }[] | null;
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
  yOffset,
  cuts,
  speakerName,
  introLogoUrl,
  fontUrls,
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
  // an inline array prop is a new object every frame — key the effect on content
  const fontKey = JSON.stringify(fontUrls ?? []);
  useEffect(() => {
    const faces: { weight: number; url: string }[] = JSON.parse(fontKey);
    if (faces.length === 0) {
      continueRender(fontHandle);
      return;
    }
    Promise.all(
      faces.map((face) =>
        // loadFont() cancels the render outright on a missing file, so probe first
        fetch(face.url, { method: "HEAD" })
          .then((r) =>
            r.ok
              ? loadFont({ family: FONT_FAMILY, url: face.url, weight: String(face.weight) })
              : console.warn(`${face.url} not found — using fallback font`),
          )
          .catch(() => console.warn(`${face.url} not reachable — using fallback font`)),
      ),
    ).finally(() => continueRender(fontHandle));
  }, [fontHandle, fontKey]);

  const pages = useMemo(
    () => (captions ? buildPages(captions, (cuts ?? []).map((sec) => sec * 1000)) : []),
    [captions, cuts],
  );

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
      {speakerName?.trim() ? (
        <IntroGraphics tSec={timeMs / 1000} speakerName={speakerName.trim()} logoUrl={introLogoUrl} />
      ) : null}
      {page ? (
        <AbsoluteFill style={{ justifyContent: "flex-end", alignItems: "center" }}>
          <div
            style={{
              // clamped so a stale or hand-edited offset can never push the
              // captions out of frame
              marginBottom: Math.max(0, Math.min(height - 200, BOTTOM_OFFSET + (yOffset ?? 0))),
              width: "84%",
              textAlign: "center",
              fontFamily: `${FONT_FAMILY}, ${FONT_FALLBACK}`,
              fontSize: FONT_SIZE,
              lineHeight: 1.2,
              textTransform: TEXT_TRANSFORM,
              color: TEXT_COLOR,
              textShadow: TEXT_SHADOW,
            }}
          >
            {page.lines.map((line, lineIndex) => {
              // whole-line reveal: a line appears as one unit slightly before
              // its first word is spoken (reading outruns listening). No entry
              // animation — a hard cut on, deliberately. Lines still to come are
              // hidden rather than unmounted, so the block never reflows; once a
              // later line starts, this one drops to the regular weight.
              const lineStart = Math.max(0, line[0].fromMs - LINE_LEAD_MS);
              const started = timeMs >= lineStart;
              const laterLineStarted = page.lines.some(
                (other, otherIndex) =>
                  otherIndex > lineIndex && timeMs >= Math.max(0, other[0].fromMs - LINE_LEAD_MS),
              );
              return (
                <div
                  key={lineIndex}
                  style={{
                    visibility: started ? "visible" : "hidden",
                    fontWeight: laterLineStarted ? FONT_WEIGHT_SPOKEN : FONT_WEIGHT_CURRENT,
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
                            textShadow: "none", // inherited from the caption block
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
