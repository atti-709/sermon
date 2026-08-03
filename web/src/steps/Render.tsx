import { useState } from "react";
import { api } from "../api";
import type { ClipState, ProjectState } from "../types";
import { JobRunner } from "../components/JobRunner";

export const Render: React.FC<{
  project: ProjectState;
  clip: ClipState;
  onRefresh: () => Promise<void>;
}> = ({ project, clip, onRefresh }) => {
  const [error, setError] = useState<string | null>(null);
  const stale = clip.rendered.exists && clip.rendered.stale;

  const chooseOutputDir = async () => {
    setError(null);
    const { path } = await api.pickFile("folder");
    if (!path) return; // dialog canceled
    try {
      await api.setOutputDir(project.id, path);
      await onRefresh();
    } catch (exc) {
      setError((exc as Error).message);
    }
  };
  // cache-buster: same URL after a re-render would let the browser show the old file
  const renderedUrl = clip.urls.rendered
    ? `${clip.urls.rendered}?v=${clip.rendered.mtime ?? 0}`
    : null;

  return (
    <>
      <h2>Render</h2>
      <p className="hint">
        Remotion burns the captions and the tracked 9:16 crop into a 1080×1920 MP4 — ready for Reels,
        Shorts and TikTok. The render always reads the latest saved captions and tracking data.
      </p>
      <div className="row" style={{ gap: 10, marginTop: 20 }}>
        <span className="hint" style={{ flexShrink: 0 }}>
          Saving to
        </span>
        <strong className="mono" style={{ overflowWrap: "anywhere", minWidth: 0 }}>
          {project.output_dir}
        </strong>
        <button className="sm" onClick={() => void chooseOutputDir()}>
          Change…
        </button>
        <button className="sm" onClick={() => api.reveal(project.output_dir)}>
          Open
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      {stale && (
        <div className="card stale-banner" style={{ marginTop: 16 }}>
          <strong>This render is out of date.</strong> The captions or tracking data changed after it
          was produced — re-render to include your latest edits.
        </div>
      )}
      <div style={{ margin: "18px 0" }}>
        <JobRunner
          key={clip.id}
          kind="render"
          projectId={project.id}
          clipId={clip.id}
          label={
            clip.rendered.exists
              ? stale
                ? "Re-render with latest edits"
                : "Re-render"
              : "Render final video"
          }
          onDone={() => void onRefresh()}
        />
      </div>
      {clip.rendered.exists && (
        <div className="card">
          <div className="row" style={{ alignItems: "flex-start", gap: 18, flexWrap: "nowrap" }}>
            {renderedUrl && (
              <video
                key={renderedUrl}
                src={renderedUrl}
                controls
                style={{ width: 200, flexShrink: 0, borderRadius: 10, background: "var(--bg-inset)" }}
              />
            )}
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start", gap: 16 }}>
                <strong style={{ minWidth: 0, overflowWrap: "anywhere" }}>
                  {clip.rendered.path.split("/").pop()}
                </strong>
                <button className="sm" onClick={() => api.reveal(clip.rendered.path)}>
                  Reveal in Finder
                </button>
              </div>
              <div className="path" style={{ marginTop: 4 }}>
                {clip.rendered.path}
              </div>
              {stale && (
                <div className="hint" style={{ marginTop: 8 }}>
                  ⚠ Rendered before the latest caption and tracking changes
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
};
