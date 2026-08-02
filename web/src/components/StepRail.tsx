import type { ProjectState } from "../types";
import { STEPS, StepKey } from "../App";

const doneMark = (project: ProjectState | null, key: StepKey): boolean => {
  if (!project) return false;
  switch (key) {
    case "video":
      return true;
    case "transcribe":
      return project.steps.transcribe === "done";
    case "highlights":
      return project.steps.highlights === "done";
    case "export":
      return project.steps.export === "done" && project.clips.length > 0;
    case "captions":
      return project.clips.some((c) => c.has_captions);
    case "preview":
      return project.clips.some((c) => c.has_captions);
    case "render":
      return project.clips.some((c) => c.rendered.exists);
  }
};

export const StepRail: React.FC<{
  project: ProjectState | null;
  step: StepKey;
  unlocked: Record<StepKey, boolean>;
  onSelect: (key: StepKey) => void;
}> = ({ project, step, unlocked, onSelect }) => (
  <nav className="rail">
    <h1>sermon</h1>
    <p className="project-name">{project ? project.video.name : "no video selected"}</p>
    {STEPS.map(({ key, title }, index) => {
      const done = doneMark(project, key);
      return (
        <button
          key={key}
          className={`rail-step${step === key ? " active" : ""}${done ? " done" : ""}`}
          disabled={!unlocked[key]}
          onClick={() => onSelect(key)}
        >
          <span className="dot">{done ? "✓" : index + 1}</span>
          <span>{title}</span>
        </button>
      );
    })}
  </nav>
);
