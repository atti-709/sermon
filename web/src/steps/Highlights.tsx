import { useEffect, useState } from "react";
import { api } from "../api";
import type { Highlight, ProjectState } from "../types";
import { JobRunner } from "../components/JobRunner";
import { SegmentPlayer } from "../components/SegmentPlayer";

const HighlightCard: React.FC<{
  highlight: Highlight;
  rank: number;
  videoUrl: string | null;
  projectId: string;
  onExported: () => void;
}> = ({ highlight: h, rank, videoUrl, projectId, onExported }) => (
  <div className="card hl-card">
    <div className="row" style={{ alignItems: "flex-start", gap: 16 }}>
      {videoUrl && (
        <SegmentPlayer
          videoUrl={videoUrl}
          startSec={h.start_sec}
          endSec={h.end_sec}
          hookStartSec={h.hook_start_sec}
          hookEndSec={h.hook_end_sec}
        />
      )}
      <div style={{ flex: 1, minWidth: 260 }}>
        <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
          <div>
            <strong>
              {rank}. {h.title}
            </strong>
            <div className="hint mono">
              {h.start} → {h.end} · {Math.round(h.duration_sec)}s (hook {h.hook_start} → {h.hook_end})
            </div>
          </div>
          <div style={{ textAlign: "right" }}>
            <div className="score">{h.virality_score}</div>
            <div className="subscores">
              <span>hook {h.scores.hook}</span>
              <span>flow {h.scores.flow}</span>
              <span>value {h.scores.value}</span>
              <span>reach {h.scores.reach}</span>
            </div>
          </div>
        </div>
        <div className="hook">“{h.hook_text}”</div>
        <p style={{ margin: "6px 0 0" }}>{h.summary}</p>
        <details>
          <summary>excerpt & scoring</summary>
          <p>{h.excerpt}</p>
          <p className="hint">{h.score_reason}</p>
        </details>
        <div style={{ marginTop: 12 }}>
          <JobRunner
            kind="export_vertical"
            projectId={projectId}
            params={{
              start_sec: Math.min(h.start_sec, h.hook_start_sec ?? h.start_sec),
              end_sec: Math.max(h.end_sec, h.hook_end_sec ?? h.end_sec),
              index: rank,
            }}
            label={h.vertical?.exists ? "Re-export vertical" : "Export vertical for DaVinci"}
            onDone={(done) => {
              if (done.state === "succeeded") onExported();
            }}
          />
          {h.vertical?.exists && (
            <div className="row" style={{ marginTop: 6, gap: 8 }}>
              <span className="chip on">vertical · tracked · 1080×1920</span>
              <span className="hint mono" style={{ wordBreak: "break-all" }}>{h.vertical.path}</span>
              <button onClick={() => api.reveal(h.vertical!.path)}>Reveal in Finder</button>
            </div>
          )}
        </div>
      </div>
    </div>
  </div>
);

export const Highlights: React.FC<{
  project: ProjectState;
  onRefresh: () => Promise<void>;
  onNext: () => void;
}> = ({ project, onRefresh, onNext }) => {
  const [count, setCount] = useState(8);
  const [minDuration, setMinDuration] = useState(20);
  const [maxDuration, setMaxDuration] = useState(110);
  const [geminiModel, setGeminiModel] = useState("gemini-flash-latest");
  const [highlights, setHighlights] = useState<Highlight[] | null>(null);
  const [keyPresent, setKeyPresent] = useState(true);
  const done = project.steps.highlights === "done";

  const videoUrl = project.video.exists
    ? `/media/project/${project.id}/${encodeURIComponent(project.video.name)}`
    : null;

  const loadHighlights = () =>
    api
      .highlights(project.id)
      .then((file) => setHighlights(file.highlights))
      .catch(() => setHighlights(null));

  useEffect(() => {
    if (done) void loadHighlights();
    api.geminiStatus().then(({ key_present }) => setKeyPresent(key_present));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project.id]);

  return (
    <>
      <h2>Highlights</h2>
      <p className="hint">
        The timestamped transcript (text only — no audio or video) goes to Gemini, which suggests clip
        candidates with a hook moment and a virality score. Preview a candidate, then{" "}
        <strong>Export vertical</strong>: the speaker gets tracked and the clip comes out as a
        1080×1920 ProRes ready for your DaVinci edit — B-roll lands in the final vertical frame.
      </p>
      {!keyPresent && (
        <p className="error">
          GEMINI_API_KEY is not set — add it to the .env file at the repo root and restart{" "}
          <code>sermon web</code>.
        </p>
      )}
      <div className="row" style={{ margin: "14px 0" }}>
        <label className="field">
          clips
          <input
            type="number"
            min={1}
            max={20}
            value={count}
            onChange={(e) => setCount(Number(e.target.value))}
            style={{ width: 64 }}
          />
        </label>
        <label className="field">
          min s
          <input
            type="number"
            value={minDuration}
            onChange={(e) => setMinDuration(Number(e.target.value))}
            style={{ width: 72 }}
          />
        </label>
        <label className="field">
          max s
          <input
            type="number"
            value={maxDuration}
            onChange={(e) => setMaxDuration(Number(e.target.value))}
            style={{ width: 72 }}
          />
        </label>
        <label className="field">
          gemini model
          <input value={geminiModel} onChange={(e) => setGeminiModel(e.target.value)} style={{ width: 180 }} />
        </label>
      </div>
      <JobRunner
        kind="highlights"
        projectId={project.id}
        params={{ count, min_duration: minDuration, max_duration: maxDuration, gemini_model: geminiModel }}
        label={done ? "Re-run highlights" : "Find highlights"}
        disabled={!keyPresent}
        onDone={(d) => {
          void onRefresh();
          if (d.state === "succeeded") void loadHighlights();
        }}
      />
      {highlights && (
        <>
          <div className="row" style={{ justifyContent: "space-between", marginTop: 24 }}>
            <h3 style={{ margin: 0 }}>
              {highlights.length} suggestions <span className="hint">(sorted by virality)</span>
            </h3>
            <button className="primary" onClick={onNext}>
              Next: Edit in Resolve →
            </button>
          </div>
          {highlights.map((h, i) => (
            <HighlightCard
              key={`${h.start_sec}-${i}`}
              highlight={h}
              rank={i + 1}
              videoUrl={videoUrl}
              projectId={project.id}
              onExported={() => void loadHighlights()}
            />
          ))}
        </>
      )}
    </>
  );
};
