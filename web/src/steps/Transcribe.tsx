import { useEffect, useState } from "react";
import { api, fmtTime } from "../api";
import type { ProjectState, SegmentsFile } from "../types";
import { JobRunner } from "../components/JobRunner";

export const Transcribe: React.FC<{
  project: ProjectState;
  onRefresh: () => Promise<void>;
  onNext: () => void;
}> = ({ project, onRefresh, onNext }) => {
  const [model, setModel] = useState("large-v3-turbo");
  const [language, setLanguage] = useState("sk");
  const [transcript, setTranscript] = useState<SegmentsFile | null>(null);
  const done = project.steps.transcribe === "done";

  useEffect(() => {
    if (done) {
      api.transcript(project.id).then(setTranscript).catch(() => setTranscript(null));
    }
  }, [done, project.id]);

  return (
    <>
      <h2>Transcribe</h2>
      <p className="hint">
        Runs locally on the Apple GPU (mlx-whisper). A 45-minute sermon takes a few minutes with{" "}
        <code>large-v3-turbo</code>; <code>large-v3</code> is a bit more accurate for Slovak but ~4×
        slower. First run downloads ~1.6 GB of model weights — watch the log below.
      </p>
      <div className="row" style={{ margin: "14px 0" }}>
        <label className="field">
          model
          <select value={model} onChange={(e) => setModel(e.target.value)}>
            <option>large-v3-turbo</option>
            <option>large-v3</option>
            <option>medium</option>
            <option>small</option>
          </select>
        </label>
        <label className="field">
          language
          <input value={language} onChange={(e) => setLanguage(e.target.value)} style={{ width: 60 }} />
        </label>
      </div>
      <JobRunner
        kind="transcribe"
        projectId={project.id}
        params={{ model, language }}
        label={done ? "Re-transcribe" : "Start transcription"}
        onDone={(d) => {
          void onRefresh();
          if (d.state === "succeeded") void api.transcript(project.id).then(setTranscript);
        }}
      />
      {done && transcript && (
        <>
          <div className="row" style={{ justifyContent: "space-between", marginTop: 24 }}>
            <h3 style={{ margin: 0 }}>
              Transcript <span className="hint">({transcript.segments.length} segments, {transcript.model})</span>
            </h3>
            <button className="primary" onClick={onNext}>
              Next: Highlights →
            </button>
          </div>
          <div className="card transcript">
            {transcript.segments.map((s, i) => (
              <div key={i}>
                <span className="ts">{fmtTime(s.start)}</span>
                {s.text}
              </div>
            ))}
          </div>
        </>
      )}
    </>
  );
};
