import { api } from "../api";
import type { ClipState, ProjectState } from "../types";
import { JobRunner } from "../components/JobRunner";

export const Render: React.FC<{
  project: ProjectState;
  clip: ClipState;
  onRefresh: () => Promise<void>;
}> = ({ project, clip, onRefresh }) => {
  const stale = clip.rendered.exists && clip.rendered.stale;
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
      {stale && (
        <div className="card stale-banner">
          <strong>This render is out of date.</strong> The captions or tracking data changed after it
          was produced — re-render to include your latest edits.
        </div>
      )}
      <div style={{ margin: "14px 0" }}>
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
          <div className="row" style={{ justifyContent: "space-between" }}>
            <div>
              <strong>{clip.rendered.path.split("/").pop()}</strong>
              <div className="hint mono">{clip.rendered.path}</div>
              {stale && <div className="hint">⚠ rendered before the latest caption/tracking changes</div>}
            </div>
            <button onClick={() => api.reveal(clip.rendered.path)}>Reveal in Finder</button>
          </div>
          {renderedUrl && (
            <video
              key={renderedUrl}
              src={renderedUrl}
              controls
              style={{ maxWidth: 300, marginTop: 12, borderRadius: 10 }}
            />
          )}
        </div>
      )}
    </>
  );
};
