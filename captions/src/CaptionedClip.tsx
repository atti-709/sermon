import { Caption } from "@remotion/captions";
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
const LINE_MAX_CHARS = 24; // per caption line, at FONT_SIZE inside the 84%-wide block
const PAGE_HOLD_MS = 300; // keep a finished page visible briefly after the last word
const LINE_POP_MS = 200; // entry animation length per line
const DIMMED_LINE_OPACITY = 0.6; // a line fades to this once a later line starts
// ---------------------------------------------------------------------------

// Slovak captioning conventions: max two lines per page, bottom-heavy split
// (second line at least as long as the first), a sentence end always ends its
// line, and one-letter prepositions/conjunctions (k, s, v, z, a, i, o, u)
// never dangle at the end of a line.
type PageToken = { text: string; fromMs: number; toMs: number };
type Page = { startMs: number; endMs: number; lines: PageToken[][] };

const ONE_LETTER_WORDS = new Set(["a", "i", "k", "o", "s", "u", "v", "z"]);

const lineLen = (line: PageToken[]) =>
  line.reduce((n, t) => n + t.text.length, 0) + Math.max(0, line.length - 1);

const isSentenceEnd = (text: string) => /[.!?…]["')]*$/.test(text);

const endsWithOrphan = (line: PageToken[]) =>
  line.length > 0 &&
  ONE_LETTER_WORDS.has(line[line.length - 1].text.replace(/[^\p{L}]/gu, "").toLowerCase()) &&
  line[line.length - 1].text.replace(/[^\p{L}]/gu, "").length === 1 &&
  !/[.!?…,;:]$/.test(line[line.length - 1].text);

// bottom-heavy two-line split: line1 <= line2, no orphan at the end of line1,
// as balanced as possible, with a slight preference for breaking after a comma
const balance = (tokens: PageToken[]): [PageToken[], PageToken[]] => {
  let best: [PageToken[], PageToken[]] | null = null;
  let bestScore = Infinity;
  for (let cut = 1; cut < tokens.length; cut++) {
    const a = tokens.slice(0, cut);
    const b = tokens.slice(cut);
    const la = lineLen(a);
    const lb = lineLen(b);
    if (la > LINE_MAX_CHARS || lb > LINE_MAX_CHARS || endsWithOrphan(a)) continue;
    let score = (la <= lb ? 0 : 1000) + Math.abs(lb - la);
    if (/[,;:]$/.test(a[a.length - 1].text)) score -= 2;
    if (score < bestScore) {
      bestScore = score;
      best = [a, b];
    }
  }
  const mid = Math.ceil(tokens.length / 2);
  return best ?? [tokens.slice(0, mid), tokens.slice(mid)];
};

const buildPages = (captions: Caption[]): Page[] => {
  const tokens: PageToken[] = captions.map((c) => ({
    text: c.text.trim(),
    fromMs: c.startMs,
    toMs: c.endMs,
  }));

  // sentences never share a line: a period/!/? closes its line
  const sentences: PageToken[][] = [];
  let sentence: PageToken[] = [];
  for (const t of tokens) {
    sentence.push(t);
    if (isSentenceEnd(t.text)) {
      sentences.push(sentence);
      sentence = [];
    }
  }
  if (sentence.length) sentences.push(sentence);

  // greedy-wrap each sentence into lines, then pull orphaned prepositions down
  const lines: { tokens: PageToken[]; sentence: number }[] = [];
  sentences.forEach((s, id) => {
    let cur: PageToken[] = [];
    for (const t of s) {
      if (cur.length && lineLen([...cur, t]) > LINE_MAX_CHARS) {
        if (endsWithOrphan(cur)) {
          const orphan = cur.pop()!;
          lines.push({ tokens: cur, sentence: id });
          cur = [orphan];
        } else {
          lines.push({ tokens: cur, sentence: id });
          cur = [];
        }
      }
      cur.push(t);
    }
    if (cur.length) lines.push({ tokens: cur, sentence: id });
  });

  // pair consecutive lines into two-line pages; rebalance pairs that belong
  // to the same sentence so the second line ends up the longer one
  const pages: Page[] = [];
  for (let i = 0; i < lines.length; ) {
    const first = lines[i];
    const second = i + 1 < lines.length ? lines[i + 1] : undefined;
    let pageLines: PageToken[][];
    if (second) {
      pageLines =
        first.sentence === second.sentence
          ? balance([...first.tokens, ...second.tokens])
          : [first.tokens, second.tokens];
      i += 2;
    } else {
      pageLines = [first.tokens];
      i += 1;
    }
    const flat = pageLines.flat();
    pages.push({
      startMs: flat[0].fromMs,
      endMs: flat[flat.length - 1].toMs + PAGE_HOLD_MS,
      lines: pageLines,
    });
  }

  // bridge short gaps so captions don't flicker between pages
  for (let i = 0; i < pages.length - 1; i++) {
    const gap = pages[i + 1].startMs - pages[i].endMs;
    if (gap > 0 && gap < 1000) pages[i].endMs = pages[i + 1].startMs;
    pages[i].endMs = Math.min(pages[i].endMs, pages[i + 1].startMs);
  }
  return pages;
};

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

  const pages = useMemo(() => (captions ? buildPages(captions) : []), [captions]);

  // word currently being edited, identified by its start timestamp
  const [editing, setEditing] = useState<{ fromMs: number; text: string } | null>(null);

  // Studio's selection tool captures canvas clicks via React synthetic events,
  // so word spans never receive them. Native capture-phase listeners on window
  // run before any React handler: when the press lands on a caption word we
  // claim it for editing; everything else (timeline, scrubbing) is untouched.
  useEffect(() => {
    if (!editable) return;
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
  }, [editable]);

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

  const page = pages.find((p) => timeMs >= p.startMs && timeMs < p.endMs);

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
            {page.lines.map((line, lineIndex) => {
              // whole-line reveal: a line pops in as one unit when its first
              // word is spoken; once a later line starts, this one de-emphasizes
              const lineStart = line[0].fromMs;
              const started = timeMs >= lineStart;
              const laterLineStarted = page.lines.some(
                (other, otherIndex) => otherIndex > lineIndex && timeMs >= other[0].fromMs,
              );
              const progress = spring({
                frame,
                fps,
                delay: (lineStart / 1000) * fps,
                durationInFrames: (LINE_POP_MS / 1000) * fps,
                config: { damping: 200 },
              });
              // in Studio, not-yet-spoken lines stay faintly visible so their
              // words can be clicked and corrected; renders keep them hidden
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
