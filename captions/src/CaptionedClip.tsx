import { Caption } from "@remotion/captions";
import { writeStaticFile } from "@remotion/studio";
import { getRemotionEnvironment, staticFile } from "remotion";
import { CaptionedClipCore, type Framing } from "./CaptionedClipCore";

// Thin Remotion Studio wrapper around CaptionedClipCore: resolves media via
// staticFile() and persists in-Studio word corrections straight into public/.
// The sermon web app renders the core directly with its own URLs + save API.

const FONT_FILE = "fonts/Aspekta-600.ttf";

export const captionsFileFor = (src: string) => src.replace(/\.[^.]+$/, "") + ".captions.json";
export const framingFileFor = (src: string) => src.replace(/\.[^.]+$/, "") + ".framing.json";

export type { Framing };

export const CaptionedClip: React.FC<{
  src: string;
  captions: Caption[] | null;
  framing?: Framing | null;
}> = ({ src, captions, framing }) => {
  const { isStudio, isRendering } = getRemotionEnvironment();
  const editable = isStudio && !isRendering;

  return (
    <CaptionedClipCore
      videoSrc={staticFile(src)}
      captions={captions}
      framing={framing}
      fontUrl={staticFile(FONT_FILE)}
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
