import { useEffect, useState } from "react";
import { api } from "../api";
import type { Correction, Framing, ProjectState } from "../types";
import { JobRunner } from "../components/JobRunner";

export const Clips: React.FC<{
  project: ProjectState;
  clipId: string | null;
  onSelectClip: (id: string) => void;
  onRefresh: () => Promise<void>;
  onNext: () => void;
}> = ({ project, clipId, onSelectClip, onRefresh, onNext }) => {
  const [proofread, setProofread] = useState(true);
  const [track, setTrack] = useState(true);
  const [corrections, setCorrections] = useState<Correction[] | null>(null);
  const [framing, setFraming] = useState<Framing | null>(null);
  const [autoTrack, setAutoTrack] = useState(false);
  const clip = project.clips.find((c) => c.id === clipId) ?? null;

  // persisted results (corrections file, framing keyframes) reload on every
  // visit — navigating away and back must not lose them
  useEffect(() => {
    setCorrections(null);
    setFraming(null);
    if (!clip) return;
    if (clip.has_corrections) {
      api
        .getCorrections(project.id, clip.id)
        .then((file) => setCorrections(file.corrections))
        .catch(() => setCorrections(null));
    }
    if (clip.has_framing) {
      api
        .getFraming(project.id, clip.id)
        .then(setFraming)
        .catch(() => setFraming(null));
    }
  }, [project.id, clip?.id, clip?.has_corrections, clip?.has_framing]);

  if (project.clips.length === 0) {
    return (
      <>
        <h2>Captions & Tracking</h2>
        <p className="hint">No rendered clips registered yet — add one on the “Edit in Resolve” step.</p>
      </>
    );
  }

  return (
    <>
      <h2>Captions & Tracking</h2>
      <p className="hint">
        WhisperX generates word-level timestamps on the CPU (~1 min per clip), Gemini proofreads the
        Slovak (word-for-word repairs only), and Apple Vision tracks the preacher on the Neural Engine so
        the 9:16 crop follows them — calm hold-and-pan, no jitter.
      </p>
      <div className="row" style={{ margin: "14px 0" }}>
        <label className="field">
          clip
          <select value={clipId ?? ""} onChange={(e) => onSelectClip(e.target.value)}>
            {project.clips.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        <label className="field" style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
          <input type="checkbox" checked={proofread} onChange={(e) => setProofread(e.target.checked)} />
          Gemini proofread
        </label>
        <label className="field" style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
          <input type="checkbox" checked={track} onChange={(e) => setTrack(e.target.checked)} />
          speaker tracking
        </label>
      </div>
      {clip && (
        <>
          <div style={{ marginBottom: 4 }}>
            <span className={`chip${clip.has_captions ? " on" : ""}`}>captions</span>
            <span className={`chip${clip.has_framing ? " on" : ""}`}>tracking</span>
          </div>
          <JobRunner
            key={`captions-${clip.id}`}
            kind="captions"
            projectId={project.id}
            clipId={clip.id}
            params={{ proofread }}
            label={clip.has_captions ? "Re-run captions" : "Generate captions"}
            onDone={(done) => {
              void onRefresh();
              if (done.state === "succeeded") {
                const result = done.result as { corrections?: Correction[] } | null;
                setCorrections(result?.corrections ?? []);
                if (track) setAutoTrack(true);
              }
            }}
          />
          {corrections && corrections.length > 0 && (
            <div className="card">
              <strong>Proofread corrections</strong>
              <table className="corrections">
                <tbody>
                  {corrections.map((c) => (
                    <tr key={c.index}>
                      <td className="before">{c.before}</td>
                      <td className="after">{c.after || "(removed)"}</td>
                      <td className="hint">{c.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {corrections && corrections.length === 0 && (
            <p className="hint">Proofread: no corrections needed.</p>
          )}
          {(autoTrack || clip.has_captions) && (
            <div style={{ marginTop: 18 }}>
              <h3>Speaker tracking</h3>
              {framing && (
                <p className="hint">
                  Tracking data: {framing.keyframes.length} camera keyframes
                  {framing.cuts && framing.cuts.length > 0
                    ? ` · snaps at ${framing.cuts.map((c) => `${Math.round(c)}s`).join(", ")}`
                    : " · no hard cuts"}
                  {framing.duration ? ` over ${Math.round(framing.duration)}s` : ""}
                </p>
              )}
              <JobRunner
                key={`track-${clip.id}-${autoTrack}`}
                kind="track"
                projectId={project.id}
                clipId={clip.id}
                autoStart={autoTrack}
                label={clip.has_framing ? "Re-run tracking" : "Track speaker"}
                onDone={() => {
                  setAutoTrack(false);
                  void onRefresh();
                }}
              />
            </div>
          )}
          {clip.has_captions && (
            <div className="row" style={{ marginTop: 24, justifyContent: "flex-end" }}>
              <button className="primary" onClick={onNext}>
                Next: Preview & Edit →
              </button>
            </div>
          )}
        </>
      )}
    </>
  );
};
