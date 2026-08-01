import { Caption } from "@remotion/captions";
import { getVideoMetadata } from "@remotion/media-utils";
import { CalculateMetadataFunction, Composition, staticFile } from "remotion";
import { z } from "zod";
import { CaptionedClip, captionsFileFor } from "./CaptionedClip";

const FPS = 30;

export const schema = z.object({
  // filename of a clip inside public/ — its captions are expected at <name>.captions.json
  src: z.string(),
  captions: z.array(z.any()).nullable(),
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
  return {
    durationInFrames: Math.ceil(meta.durationInSeconds * FPS),
    fps: FPS,
    width: 1080,
    height: 1920,
    props: { ...props, captions },
  };
};

export const Root: React.FC = () => {
  return (
    <Composition
      id="CaptionedClip"
      component={CaptionedClip}
      schema={schema}
      defaultProps={{"src":"2 Miro Toth_short highlights.mov","captions":null}}
      durationInFrames={30 * 10}
      fps={FPS}
      width={1080}
      height={1920}
      calculateMetadata={calculateMetadata}
    />
  );
};
