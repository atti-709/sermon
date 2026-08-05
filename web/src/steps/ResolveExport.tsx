import { useEffect, useState } from "react";
import { api } from "../api";
import type { ClipState, Highlight, ProjectState } from "../types";
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
  // the exported highlights, for the folders each render is expected in
  const [exported, setExported] = useState<Highlight[]>([]);

  useEffect(() => {
    api
      .highlights(project.id)
      .then((file) => setExported(file.highlights.filter((h) => h.vertical?.exists)))
      .catch(() => setExported([]));
  }, [project.id]);

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

  const addClipNative = async (startPath?: string) => {
    setDialogOpen(true);
    setError(null);
    try {
      const { path } = await api.pickFile("clip", startPath);
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
        Bring the <strong>vertical tracked clips</strong> you exported on the Highlights step into a
        1080×1920 timeline — B-roll and titles land directly in the final vertical frame.
      </p>
      <div className="card" style={{ marginTop: 18 }}>
        <ol className="instructions">
          <li>
            Create a 1080×1920 timeline and drop in the clip from the highlight's{" "}
            <code>01_VERTICAL_NO_CAPTION</code> folder.
          </li>
          <li>
            Open with the hook: duplicate the hook moment to the front, then the full passage. Roll and
            trim freely — the export covers the whole highlight range.
          </li>
          <li>Add B-roll, trims, whatever the clip needs. Aim for ~1:30, cap 1:50.</li>
          <li>
            Render vertical (1080×1920 — captions come next) into that highlight's{" "}
            <code>02_DAVINCI_EXPORT</code> folder, then register it below.
          </li>
        </ol>
      </div>

      {exported.length > 0 && (
        <div className="section">
          <div className="section-head">
            <h3>Render into</h3>
            <span className="hint">one folder per exported highlight</span>
          </div>
          {exported.map((h) => (
            <div className="card" key={h.folder ?? h.title}>
              <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start", gap: 12 }}>
                <div style={{ minWidth: 0 }}>
                  <strong style={{ overflowWrap: "anywhere" }}>{h.title}</strong>
                  <div className="path" style={{ marginTop: 2 }}>
                    {h.davinci_dir}
                  </div>
                </div>
                <span className="row" style={{ gap: 8, flexShrink: 0 }}>
                  <button className="sm" onClick={() => h.davinci_dir && api.reveal(h.davinci_dir)}>
                    Open
                  </button>
                  <button
                    className="sm"
                    disabled={dialogOpen}
                    onClick={() => void addClipNative(h.davinci_dir)}
                  >
                    Register a render…
                  </button>
                </span>
              </div>
            </div>
          ))}
        </div>
      )}

      <details style={{ margin: "16px 0" }}>
        <summary className="hint">Backup: horizontal XML timeline of the original video</summary>
        <div className="row" style={{ margin: "12px 0" }}>
          <button onClick={doExport}>{xmlPath ? "Re-export timeline XML" : "Export timeline XML"}</button>
          {xmlPath && <button onClick={() => api.reveal(xmlPath)}>Reveal in Finder</button>}
        </div>
        {xmlPath && <p className="path">{xmlPath}</p>}
        <p className="hint">
          Imports via <strong>File → Import Timeline → Import AAF, EDL, XML…</strong> — one hook clip +
          full clip per highlight, referencing the original horizontal video (the old workflow: crop
          and tracking happen after the edit instead of before).
        </p>
      </details>
      {error && <p className="error">{error}</p>}

      <div className="section">
        <div className="section-head">
          <h3>Rendered clips</h3>
          {project.clips.length > 0 && (
            <span className="hint">
              {project.clips.length} registered · captions run on these next
            </span>
          )}
        </div>
        {project.clips.length === 0 && (
          <p className="hint">
            Nothing registered yet. Render a finished edit out of Resolve, then add it here to give it
            captions.
          </p>
        )}
        {project.clips.map((clip) => (
          <div className="card" key={clip.id}>
            <strong>{clip.name}</strong>
            <div className="path" style={{ marginTop: 2 }}>
              {clip.path}
            </div>
            <div className="chip-row" style={{ marginTop: 10 }}>
              {clip.highlight && <span className="chip on">{clip.highlight}</span>}
              <span className={`chip${clip.has_captions ? " on" : ""}`}>captions</span>
              <span className={`chip${clip.has_framing ? " on" : ""}`}>tracking</span>
              <span className={`chip${clip.rendered.exists ? " on" : ""}`}>rendered</span>
            </div>
          </div>
        ))}
        <div className="row" style={{ marginTop: 14 }}>
          <button
            className="primary"
            onClick={() => void addClipNative(project.video.path.replace(/\/[^/]+$/, ""))}
            disabled={dialogOpen}
          >
            {dialogOpen ? "Finder dialog is open…" : "Add rendered clip…"}
          </button>
          {!browsing && <button onClick={() => setBrowsing(true)}>Browse inside the app</button>}
        </div>
        {browsing && (
          <div style={{ marginTop: 12 }}>
            <FileBrowser startPath={project.video.path.replace(/\/[^/]+$/, "")} onPick={addClip} />
          </div>
        )}
      </div>
    </>
  );
};
