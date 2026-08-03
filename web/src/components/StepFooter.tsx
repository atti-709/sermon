import { STEPS, StepKey } from "../App";

/** What the operator has to do before the next step opens — shown in place of
 *  the step counter so a disabled Next button is never a dead end. */
const TO_CONTINUE: Record<StepKey, string> = {
  video: "",
  transcribe: "Choose a video to continue.",
  highlights: "Transcribe the sermon to continue.",
  export: "Find highlights to continue.",
  captions: "Register a rendered clip to continue.",
  preview: "Generate captions for this clip to continue.",
  render: "Generate captions for this clip to continue.",
};

/** Back / Next for every step, in the same place on every step. */
export const StepFooter: React.FC<{
  step: StepKey;
  unlocked: Record<StepKey, boolean>;
  onSelect: (key: StepKey) => void;
}> = ({ step, unlocked, onSelect }) => {
  const index = STEPS.findIndex((s) => s.key === step);
  const previous = STEPS[index - 1];
  const next = STEPS[index + 1];
  const nextReady = next != null && unlocked[next.key];

  return (
    <div className="step-footer">
      <span className="footer-note">
        {next && !nextReady ? TO_CONTINUE[next.key] : `Step ${index + 1} of ${STEPS.length}`}
      </span>
      {previous && (
        <button onClick={() => onSelect(previous.key)}>
          <span aria-hidden="true">←</span> {previous.title}
        </button>
      )}
      {next && (
        <button className="primary" disabled={!nextReady} onClick={() => onSelect(next.key)}>
          Next: {next.title} <span aria-hidden="true">→</span>
        </button>
      )}
    </div>
  );
};
