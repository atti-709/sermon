import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "./api";
import type { ProjectState } from "./types";
import { StepRail } from "./components/StepRail";
import { PickVideo } from "./steps/PickVideo";
import { Transcribe } from "./steps/Transcribe";
import { Highlights } from "./steps/Highlights";
import { ResolveExport } from "./steps/ResolveExport";
import { Clips } from "./steps/Clips";
import { Preview } from "./steps/Preview";
import { Render } from "./steps/Render";

export type StepKey = "video" | "transcribe" | "highlights" | "export" | "captions" | "preview" | "render";

export const STEPS: { key: StepKey; title: string }[] = [
  { key: "video", title: "Video" },
  { key: "transcribe", title: "Transcribe" },
  { key: "highlights", title: "Highlights" },
  { key: "export", title: "Edit in Resolve" },
  { key: "captions", title: "Captions" },
  { key: "preview", title: "Preview & Edit" },
  { key: "render", title: "Render" },
];

const firstIncompleteStep = (project: ProjectState): StepKey => {
  if (project.steps.transcribe !== "done") return "transcribe";
  if (project.steps.highlights !== "done") return "highlights";
  if (project.clips.length === 0) return "export";
  if (!project.clips.some((c) => c.has_captions)) return "captions";
  if (project.steps.render !== "done") return "preview";
  return "render";
};

export const App: React.FC = () => {
  const [project, setProject] = useState<ProjectState | null>(null);
  const [step, setStep] = useState<StepKey>("video");
  const [clipId, setClipId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const adoptProject = useCallback((state: ProjectState, jump: boolean) => {
    setProject(state);
    setClipId((prev) => {
      if (prev && state.clips.some((c) => c.id === prev)) return prev;
      const withCaptions = state.clips.find((c) => c.has_captions);
      return (withCaptions ?? state.clips[0])?.id ?? null;
    });
    const url = new URL(window.location.href);
    url.searchParams.set("project", state.id);
    window.history.replaceState(null, "", url);
    if (jump) setStep(firstIncompleteStep(state));
  }, []);

  const refresh = useCallback(async () => {
    if (!project) return;
    setProject(await api.getProject(project.id));
  }, [project]);

  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get("project");
    if (!id) {
      setLoading(false);
      return;
    }
    api
      .getProject(id)
      .then((state) => adoptProject(state, true))
      .catch(() => undefined)
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const unlocked = useMemo((): Record<StepKey, boolean> => {
    const clip = project?.clips.find((c) => c.id === clipId) ?? null;
    return {
      video: true,
      transcribe: project != null,
      highlights: project?.steps.transcribe === "done",
      export: project?.steps.highlights === "done",
      captions: (project?.clips.length ?? 0) > 0,
      preview: clip?.has_captions ?? false,
      render: (clip?.has_captions && clip?.in_public) ?? false,
    };
  }, [project, clipId]);

  const clip = project?.clips.find((c) => c.id === clipId) ?? null;

  if (loading) return <main />;

  return (
    <>
      <StepRail project={project} step={step} unlocked={unlocked} onSelect={setStep} />
      <main>
        {step === "video" && (
          <PickVideo project={project} onPicked={(state) => adoptProject(state, true)} />
        )}
        {step === "transcribe" && project && (
          <Transcribe project={project} onRefresh={refresh} onNext={() => setStep("highlights")} />
        )}
        {step === "highlights" && project && (
          <Highlights project={project} onRefresh={refresh} onNext={() => setStep("export")} />
        )}
        {step === "export" && project && (
          <ResolveExport
            project={project}
            onRefresh={refresh}
            onClipAdded={(c) => {
              setClipId(c.id);
              void refresh();
              setStep("captions");
            }}
          />
        )}
        {step === "captions" && project && (
          <Clips
            project={project}
            clipId={clipId}
            onSelectClip={setClipId}
            onRefresh={refresh}
            onNext={() => setStep("preview")}
          />
        )}
        {step === "preview" && project && clip && (
          <Preview project={project} clip={clip} onNext={() => setStep("render")} />
        )}
        {step === "render" && project && clip && <Render project={project} clip={clip} onRefresh={refresh} />}
      </main>
    </>
  );
};
