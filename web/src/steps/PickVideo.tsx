import { useEffect, useState } from "react";
import { api } from "../api";
import type { ProjectState } from "../types";
import { FileBrowser } from "../components/FileBrowser";

export const PickVideo: React.FC<{
  project: ProjectState | null;
  onPicked: (state: ProjectState) => void;
}> = ({ project, onPicked }) => {
  const [recents, setRecents] = useState<ProjectState[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);

  useEffect(() => {
    api
      .listProjects()
      .then(({ projects }) => setRecents(projects))
      .catch(() => undefined);
  }, []);

  const pick = (path: string) =>
    api
      .createProject(path)
      .then(onPicked)
      .catch((exc) => setError(exc.message));

  const chooseNative = async () => {
    setDialogOpen(true);
    setError(null);
    try {
      const { path } = await api.pickFile("video");
      if (path) await pick(path);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setDialogOpen(false);
    }
  };

  return (
    <>
      <h2>Pick a sermon video</h2>
      <p className="hint">
        Everything the pipeline produces (transcript, highlights, Resolve timeline) lands next to the
        video file, so put it somewhere sensible first.
      </p>
      <div className="row" style={{ margin: "16px 0" }}>
        <button className="primary" onClick={chooseNative} disabled={dialogOpen}>
          {dialogOpen ? "Finder dialog is open…" : "Choose video…"}
        </button>
        {dialogOpen && <span className="hint">check your other windows if you don't see the dialog</span>}
      </div>
      {error && <p className="error">{error}</p>}
      {recents.length > 0 && (
        <>
          <h3>Recent</h3>
          {recents.map((p) => (
            <div className="card" key={p.id}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <div>
                  <strong>{p.video.name}</strong>
                  <div className="hint mono">{p.video.path}</div>
                  <div style={{ marginTop: 6 }}>
                    <span className={`chip${p.steps.transcribe === "done" ? " on" : ""}`}>transcript</span>
                    <span className={`chip${p.steps.highlights === "done" ? " on" : ""}`}>highlights</span>
                    <span className={`chip${p.clips.length > 0 ? " on" : ""}`}>
                      {p.clips.length > 0 ? `${p.clips.length} clip${p.clips.length > 1 ? "s" : ""}` : "clips"}
                    </span>
                    <span className={`chip${p.clips.some((c) => c.rendered.exists) ? " on" : ""}`}>rendered</span>
                  </div>
                </div>
                <button onClick={() => pick(p.video.path)}>Open</button>
              </div>
            </div>
          ))}
        </>
      )}
      <details style={{ marginTop: 16 }}>
        <summary className="hint" style={{ cursor: "pointer" }}>
          browse inside the app instead
        </summary>
        <div style={{ marginTop: 10 }}>
          <FileBrowser startPath={project?.video.path.replace(/\/[^/]+$/, "")} onPick={pick} />
        </div>
      </details>
    </>
  );
};
