import { useEffect, useState } from "react";
import { api } from "../api";
import type { Correction, ProjectState } from "../types";
import { JobRunner } from "../components/JobRunner";

export const Clips: React.FC<{
  project: ProjectState;
  clipId: string | null;
  onSelectClip: (id: string) => void;
  onRefresh: () => Promise<void>;
}> = ({ project, clipId, onSelectClip, onRefresh }) => {
  const [proofread, setProofread] = useState(true);
  const [corrections, setCorrections] = useState<Correction[] | null>(null);
  const clip = project.clips.find((c) => c.id === clipId) ?? null;

  // persisted proofread results reload on every visit — navigating away and
  // back must not lose them
  useEffect(() => {
    setCorrections(null);
    if (!clip) return;
    if (clip.has_corrections) {
      api
        .getCorrections(project.id, clip.id)
        .then((file) => setCorrections(file.corrections))
        .catch(() => setCorrections(null));
    }
  }, [project.id, clip?.id, clip?.has_corrections]);

  if (project.clips.length === 0) {
    return (
      <>
        <h2>Captions</h2>
        <p className="hint">
          No clips to caption yet. Go back to “Edit in Resolve” and register a clip you rendered out
          of Resolve.
        </p>
      </>
    );
  }

  return (
    <>
      <h2>Captions</h2>
      <p className="hint">
        WhisperX generates word-level timestamps on the CPU (~1 min per clip) and Gemini proofreads
        the Slovak — word-for-word repairs, filler removal and brand spellings only, never
        paraphrasing. Your clip is already vertical and tracked from the highlights export, so no
        reframing happens here.
      </p>
      <div className="form-row">
        <label className="field">
          Clip
          <select value={clipId ?? ""} onChange={(e) => onSelectClip(e.target.value)}>
            {project.clips.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        <label className="check">
          <input type="checkbox" checked={proofread} onChange={(e) => setProofread(e.target.checked)} />
          Proofread with Gemini
        </label>
        {clip && (
          <span className="control-line">
            <span className={`chip${clip.has_captions ? " on" : ""}`}>
              {clip.has_captions ? "captions ready" : "no captions yet"}
            </span>
          </span>
        )}
      </div>
      {clip && (
        <>
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
              }
            }}
          />
          {corrections && corrections.length > 0 && (
            <div className="card" style={{ marginTop: 16 }}>
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
            <p className="hint" style={{ marginTop: 16 }}>
              Proofread the transcript — nothing needed fixing.
            </p>
          )}
        </>
      )}
    </>
  );
};
