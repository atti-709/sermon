import { Caption } from "@remotion/captions";
import { writeStaticFile } from "@remotion/studio";
import { getRemotionEnvironment, staticFile } from "remotion";
import { CaptionedClipCore, type Framing } from "./CaptionedClipCore";

// Thin Remotion Studio wrapper around CaptionedClipCore: resolves media via
// staticFile() and persists in-Studio word corrections straight into public/.
// The sermon web app renders the core directly with its own URLs + save API.

const FONT_FILES = [
  { weight: 600, file: "fonts/Aspekta-600.ttf" },
  { weight: 400, file: "fonts/Aspekta-400.ttf" },
];

export const captionsFileFor = (src: string) => src.replace(/\.[^.]+$/, "") + ".captions.json";
export const framingFileFor = (src: string) => src.replace(/\.[^.]+$/, "") + ".framing.json";
/** per-clip caption style written by the web app: { "yOffset": <px> } */
export const styleFileFor = (src: string) => src.replace(/\.[^.]+$/, "") + ".style.json";
/** splice points written by the vertical export: { "cuts": [<sec>, …] } */
export const cutsFileFor = (src: string) => src.replace(/\.[^.]+$/, "") + ".cuts.json";

export type { Framing };

export const CaptionedClip: React.FC<{
  src: string;
  captions: Caption[] | null;
  framing?: Framing | null;
  yOffset?: number | null;
  cuts?: number[] | null;
}> = ({ src, captions, framing, yOffset, cuts }) => {
  const { isStudio, isRendering } = getRemotionEnvironment();
  const editable = isStudio && !isRendering;

  return (
    <CaptionedClipCore
      videoSrc={staticFile(src)}
      captions={captions}
      framing={framing}
      yOffset={yOffset}
      cuts={cuts}
      fontUrls={FONT_FILES.map(({ weight, file }) => ({ weight, url: staticFile(file) }))}
      editable={editable}
      captureClicksForStudio={editable}
      onSaveCorrections={(updated) => {
        writeStaticFile({
          filePath: captionsFileFor(src),
          contents: JSON.stringify(updated, null, 2) + "\n",
        }).catch((e) => console.error("Failed to save correction:", e));
      }}
    />
  );
};
