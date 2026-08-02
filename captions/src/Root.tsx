import { Caption } from "@remotion/captions";
import { getVideoMetadata } from "@remotion/media-utils";
import { CalculateMetadataFunction, Composition, staticFile } from "remotion";
import { z } from "zod";
import {
  CaptionedClip,
  captionsFileFor,
  cutsFileFor,
  framingFileFor,
  styleFileFor,
  type Framing,
} from "./CaptionedClip";

const FPS = 30;

export const schema = z.object({
  // filename of a clip inside public/ — its captions are expected at <name>.captions.json,
  // its (optional) subject-tracking crop path at <name>.framing.json and its (optional)
  // caption-position offset at <name>.style.json and its (optional) splice points
  // at <name>.cuts.json
  src: z.string(),
  captions: z.array(z.any()).nullable(),
  framing: z.any().nullable(),
  yOffset: z.number().nullable(),
  cuts: z.array(z.number()).nullable(),
});

type Props = z.infer<typeof schema>;

const calculateMetadata: CalculateMetadataFunction<Props> = async ({ props }) => {
  const meta = await getVideoMetadata(staticFile(props.src));
  const res = await fetch(staticFile(captionsFileFor(props.src)));
  if (!res.ok) {
    throw new Error(
      `Missing ${captionsFileFor(props.src)} in public/ — run \`sermon captions <clip>\` first`,
    );
  }
  const captions = (await res.json()) as Caption[];

  // tracking data is optional — without it the crop stays centered
  let framing: Framing | null = null;
  try {
    const framingRes = await fetch(staticFile(framingFileFor(props.src)));
    if (framingRes.ok) framing = (await framingRes.json()) as Framing;
  } catch {
    framing = null;
  }

  // caption position: an explicit prop (the render job passes one) wins over the
  // sidecar the web app's slider writes; without either the default height applies
  let yOffset = props.yOffset;
  if (yOffset == null) {
    try {
      const styleRes = await fetch(staticFile(styleFileFor(props.src)));
      if (styleRes.ok) yOffset = ((await styleRes.json()) as { yOffset?: number }).yOffset ?? null;
    } catch {
      yOffset = null;
    }
  }

  // splice points of a hook-first export — no caption page may span one
  let cuts = props.cuts;
  if (cuts == null) {
    try {
      const cutsRes = await fetch(staticFile(cutsFileFor(props.src)));
      if (cutsRes.ok) cuts = ((await cutsRes.json()) as { cuts?: number[] }).cuts ?? null;
    } catch {
      cuts = null;
    }
  }

  return {
    durationInFrames: Math.ceil(meta.durationInSeconds * FPS),
    fps: FPS,
    width: 1080,
    height: 1920,
    props: { ...props, captions, framing, yOffset, cuts },
  };
};

export const Root: React.FC = () => {
  return (
    <Composition
      id="CaptionedClip"
      component={CaptionedClip}
      schema={schema}
      defaultProps={{"src":"clip.mov","captions":null,"framing":null,"yOffset":null,"cuts":null}}
      durationInFrames={30 * 10}
      fps={FPS}
      width={1080}
      height={1920}
      calculateMetadata={calculateMetadata}
    />
  );
};
