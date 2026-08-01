import { Caption, createTikTokStyleCaptions } from "@remotion/captions";
import { loadFont } from "@remotion/fonts";
import { writeStaticFile } from "@remotion/studio";
import { useEffect, useMemo, useState } from "react";
import {
  AbsoluteFill,
  OffthreadVideo,
  continueRender,
  delayRender,
  getRemotionEnvironment,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

// ---------------------------------------------------------------------------
// Hard-coded caption style — tweak here, not via props.
// Drop your brand font at public/fonts/brand.otf (or .ttf and adjust below);
// until then the fallback stack is used.
const FONT_FAMILY = "Aspekta";
const FONT_FALLBACK = "'Arial Black', 'Helvetica Neue', sans-serif";
const FONT_FILE = "fonts/Aspekta-600.ttf";
const FONT_WEIGHT = 600;
const FONT_SIZE = 64;
const TEXT_TRANSFORM: React.CSSProperties["textTransform"] = "none";
const TEXT_COLOR = "white";
const STROKE = "12px black";
const BOTTOM_OFFSET = 400; // px from the bottom of the 1920px frame
const PAGE_MAX_MS = 1200; // how many ms of words are grouped on one page (≈3-5 words)
const WORD_POP_MS = 150; // entry animation length per word
// ---------------------------------------------------------------------------

export const captionsFileFor = (src: string) => src.replace(/\.[^.]+$/, "") + ".captions.json";

export const CaptionedClip: React.FC<{
  src: string;
  captions: Caption[] | null;
}> = ({ src, captions: captionsFromProps }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const timeMs = (frame / fps) * 1000;
  const { isStudio, isRendering } = getRemotionEnvironment();
  const editable = isStudio && !isRendering;

  // corrections applied in Studio show up immediately via this local copy;
  // the JSON in public/ is updated in the background by writeStaticFile
  const [edited, setEdited] = useState<{ src: string; captions: Caption[] } | null>(null);
  const captions = edited?.src === src ? edited.captions : captionsFromProps;

  const [fontHandle] = useState(() => delayRender("loading brand font"));
  useEffect(() => {
    // loadFont() cancels the render outright on a missing file, so probe first
    fetch(staticFile(FONT_FILE), { method: "HEAD" })
      .then((r) =>
        r.ok
          ? loadFont({ family: FONT_FAMILY, url: staticFile(FONT_FILE) })
          : console.warn(`${FONT_FILE} not found — using fallback font`),
      )
      .catch(() => console.warn(`${FONT_FILE} not reachable — using fallback font`))
      .finally(() => continueRender(fontHandle));
  }, [fontHandle]);

  const pages = useMemo(() => {
    if (!captions) return [];
    return createTikTokStyleCaptions({
      captions,
      combineTokensWithinMilliseconds: PAGE_MAX_MS,
    }).pages;
  }, [captions]);

  // word currently being edited, identified by its start timestamp
  const [editing, setEditing] = useState<{ fromMs: number; text: string } | null>(null);

  const saveCorrection = (fromMs: number, corrected: string) => {
    setEditing(null);
    if (!captions || corrected.trim() === "") return;
    const index = captions.findIndex((c) => c.startMs === fromMs);
    if (index === -1 || corrected.trim() === captions[index].text.trim()) return;
    const leadingSpace = captions[index].text.startsWith(" ") ? " " : "";
    const updated = captions.map((c, i) =>
      i === index ? { ...c, text: leadingSpace + corrected.trim() } : c,
    );
    setEdited({ src, captions: updated });
    writeStaticFile({
      filePath: captionsFileFor(src),
      contents: JSON.stringify(updated, null, 2) + "\n",
    }).catch((e) => console.error("Failed to save correction:", e));
  };

  const page = pages.find((p) => timeMs >= p.startMs && timeMs < p.startMs + p.durationMs);

  return (
    <AbsoluteFill style={{ backgroundColor: "black" }} showInTimeline={false}>
      <OffthreadVideo
        src={staticFile(src)}
        style={{ width: "100%", height: "100%", objectFit: "cover" }}
      />
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
            {page.tokens.map((token, i) => {
              // progressive reveal: words appear one at a time and stay;
              // layout is laid out up front so words don't shift
              const spoken = timeMs >= token.fromMs;
              const progress = spring({
                frame,
                fps,
                delay: (token.fromMs / 1000) * fps,
                durationInFrames: (WORD_POP_MS / 1000) * fps,
                config: { damping: 200 },
              });
              // in Studio, not-yet-spoken words stay faintly visible so they can
              // be clicked and corrected; renders keep them fully hidden
              const ghost = editable && !spoken;
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
                  onClick={
                    editable
                      ? (e) => {
                          e.stopPropagation();
                          setEditing({ fromMs: token.fromMs, text: token.text });
                        }
                      : undefined
                  }
                  style={{
                    display: "inline-block",
                    whiteSpace: "pre",
                    visibility: spoken || ghost ? "visible" : "hidden",
                    opacity: ghost ? 0.3 : progress,
                    transform: ghost ? undefined : `scale(${interpolate(progress, [0, 1], [0.85, 1])})`,
                    WebkitTextStroke: STROKE,
                    paintOrder: "stroke fill",
                    textShadow: "0 4px 16px rgba(0,0,0,0.45)",
                    cursor: editable ? "pointer" : undefined,
                  }}
                >
                  {token.text}
                </span>
              );
            })}
          </div>
        </AbsoluteFill>
      ) : null}
    </AbsoluteFill>
  );
};
