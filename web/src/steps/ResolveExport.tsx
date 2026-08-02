import { useState } from "react";
import { api } from "../api";
import type { ClipState, ProjectState } from "../types";
import { FileBrowser } from "../components/FileBrowser";

export const ResolveExport: React.FC<{
  project: ProjectState;
  onRefresh: () => Promise<void>;
  onClipAdded: (clip: ClipState) => void;
}> = ({ project, onRefresh, onClipAdded }) => {
  const [xmlPath, setXmlPath] = useState<string | null>(
    project.artifacts.resolve_xml.exists ? project.artifacts.resolve_xml.path : null,
  );
  const [error, setError] = useState<string | null>(null);
  const [browsing, setBrowsing] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);

  const doExport = () =>
    api
      .exportXml(project.id)
      .then(({ xml_path }) => {
        setXmlPath(xml_path);
        setError(null);
        void onRefresh();
      })
      .catch((exc) => setError(exc.message));

  const addClip = (path: string) =>
    api
      .addClip(project.id, path)
      .then((clip) => {
        setBrowsing(false);
        setError(null);
        onClipAdded(clip);
      })
      .catch((exc) => setError(exc.message));

  const addClipNative = async () => {
    setDialogOpen(true);
    setError(null);
    try {
      const { path } = await api.pickFile("clip");
      if (path) await addClip(path);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setDialogOpen(false);
    }
  };

  return (
    <>
      <h2>Edit in DaVinci Resolve</h2>
      <p className="hint">
        The highlights become an XML timeline referencing the original video — one hook clip + full clip
        per highlight, back-to-back, nothing re-encoded.
      </p>
      <div className="row" style={{ margin: "14px 0" }}>
        <button className="primary" onClick={doExport}>
          {xmlPath ? "Re-export timeline XML" : "Export timeline XML"}
        </button>
        {xmlPath && <button onClick={() => api.reveal(xmlPath)}>Reveal in Finder</button>}
      </div>
      {xmlPath && <p className="hint mono">{xmlPath}</p>}
      {error && <p className="error">{error}</p>}

      <div className="card">
        <ol className="instructions" style={{ margin: 0, paddingLeft: 20 }}>
          <li>
            In Resolve: <strong>File → Import Timeline → Import AAF, EDL, XML…</strong> and pick the
            exported file.
          </li>
          <li>
            Each highlight is two clips: the <em>hook</em> (the clip's opener) and the full passage. Roll
            the edges to fine-tune cut points — the full source is linked.
          </li>
          <li>Add B-roll, trims, whatever the clip needs. Aim for ~1:30, cap 1:50.</li>
          <li>
            Render each finished clip (16:9 is fine — the vertical crop and captions come next), then
            register it below.
          </li>
        </ol>
      </div>

      <h3>Rendered clips</h3>
      {project.clips.map((clip) => (
        <div className="card" key={clip.id}>
          <strong>{clip.name}</strong>
          <div className="hint mono">{clip.path}</div>
          <div style={{ marginTop: 6 }}>
            <span className={`chip${clip.has_captions ? " on" : ""}`}>captions</span>
            <span className={`chip${clip.has_framing ? " on" : ""}`}>tracking</span>
            <span className={`chip${clip.rendered.exists ? " on" : ""}`}>rendered</span>
          </div>
        </div>
      ))}
      <div className="row">
        <button className="primary" onClick={addClipNative} disabled={dialogOpen}>
          {dialogOpen ? "Finder dialog is open…" : "+ Add rendered clip…"}
        </button>
        {!browsing && (
          <button onClick={() => setBrowsing(true)}>browse inside the app</button>
        )}
      </div>
      {browsing && (
        <div style={{ marginTop: 10 }}>
          <FileBrowser startPath={project.video.path.replace(/\/[^/]+$/, "")} onPick={addClip} />
        </div>
      )}
    </>
  );
};
