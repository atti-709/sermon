import { useEffect, useRef, useState } from "react";
import { api, fmtTime } from "../api";
import type { Highlight, ProjectState } from "../types";
import { JobRunner } from "../components/JobRunner";

/** Seekable preview of one highlight range inside the full sermon video.
 *  The media route supports HTTP Range, so the browser only fetches what it
 *  shows; #t=start,end frames the initial poster at the clip's first frame. */
const HighlightPreview: React.FC<{ videoUrl: string; highlight: Highlight }> = ({
  videoUrl,
  highlight: h,
}) => {
  const ref = useRef<HTMLVideoElement>(null);
  const stopAt = useRef<number | null>(null);

  const playRange = (from: number, to: number) => {
    const video = ref.current;
    if (!video) return;
    stopAt.current = to;
    video.currentTime = from;
    void video.play();
  };

  return (
    <div className="hl-preview">
      <video
        ref={ref}
        src={`${videoUrl}#t=${h.start_sec},${h.end_sec}`}
        preload="metadata"
        controls
        onTimeUpdate={(e) => {
          const video = e.currentTarget;
          if (stopAt.current != null && video.currentTime >= stopAt.current) {
            video.pause();
            stopAt.current = null;
          }
        }}
        onPlay={(e) => {
          // native play control: respect the clip's end unless a button set its own stop
          if (stopAt.current == null && e.currentTarget.currentTime < h.end_sec) {
            stopAt.current = h.end_sec;
          }
        }}
      />
      <div className="row" style={{ gap: 6, marginTop: 6 }}>
        <button onClick={() => playRange(h.hook_start_sec, h.hook_end_sec)}>
          ▶ hook · {Math.round(h.hook_duration_sec)}s
        </button>
        <button onClick={() => playRange(h.start_sec, h.end_sec)}>
          ▶ full · {fmtTime(h.duration_sec)}
        </button>
      </div>
    </div>
  );
};

const HighlightCard: React.FC<{ highlight: Highlight; rank: number; videoUrl: string | null }> = ({
  highlight: h,
  rank,
  videoUrl,
}) => (
  <div className="card hl-card">
    <div className="row" style={{ alignItems: "flex-start", gap: 16 }}>
      {videoUrl && <HighlightPreview videoUrl={videoUrl} highlight={h} />}
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
        candidates with a hook moment and a virality score. Shorter is better: aim for ~1:30. Re-running
        is cheap — tweak and retry freely.
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
            <HighlightCard key={`${h.start_sec}-${i}`} highlight={h} rank={i + 1} videoUrl={videoUrl} />
          ))}
        </>
      )}
    </>
  );
};
