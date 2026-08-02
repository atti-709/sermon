import { useEffect, useRef, useState } from "react";
import { api, fmtTime } from "../api";
import type { Highlight, ProjectState } from "../types";
import { JobRunner } from "../components/JobRunner";
import { SegmentPlayer } from "../components/SegmentPlayer";

/** how far the out point may be dragged, as seconds from the highlight's start */
const MIN_CLIP_SEC = 10;
const MAX_CLIP_SEC = 200;

/** HH:MM:SS, matching the timestamps the highlights file carries */
const fmtClock = (sec: number): string => {
  const s = Math.max(0, Math.floor(sec));
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(Math.floor(s / 3600))}:${pad(Math.floor((s % 3600) / 60))}:${pad(s % 60)}`;
};

/** Export range for one highlight: hook-first cuts the hook moment in front of the
 *  passage, otherwise the passage is widened to cover a hook that falls outside it. */
const exportParams = (h: Highlight, rank: number, hookFirst: boolean, endSec: number) => {
  const hasHook = h.hook_start_sec != null && h.hook_end_sec != null;
  if (hookFirst && hasHook) {
    return {
      start_sec: h.start_sec,
      end_sec: endSec,
      hook_start_sec: h.hook_start_sec,
      hook_end_sec: h.hook_end_sec,
      index: rank,
    };
  }
  return {
    start_sec: Math.min(h.start_sec, h.hook_start_sec ?? h.start_sec),
    end_sec: Math.max(endSec, h.hook_end_sec ?? endSec),
    index: rank,
  };
};

const HighlightCard: React.FC<{
  highlight: Highlight;
  rank: number;
  videoUrl: string | null;
  videoDuration: number | null;
  projectId: string;
  hookFirst: boolean;
  maxDuration: number;
  onExported: () => void;
}> = ({ highlight: h, rank, videoUrl, videoDuration, projectId, hookFirst, maxDuration, onExported }) => {
  // the out point is edited here and saved back into the highlights file, so the
  // export, the Resolve XML and the notes all follow it
  const [endSec, setEndSec] = useState(h.end_sec);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const saveTimer = useRef<number | null>(null);

  const maxEnd = Math.min(videoDuration ?? h.end_sec + 60, h.start_sec + MAX_CLIP_SEC);
  const minEnd = Math.min(h.start_sec + MIN_CLIP_SEC, maxEnd);

  const changeEnd = (value: number) => {
    const clamped = Math.min(Math.max(value, minEnd), maxEnd);
    setEndSec(clamped);
    setSaveState("saving");
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => {
      api
        .setHighlightEnd(projectId, rank, clamped)
        .then((updated) => {
          setEndSec(updated.end_sec); // the server clamps to the video's length
          setSaveState("saved");
        })
        .catch(() => setSaveState("error"));
    }, 400);
  };

  const duration = endSec - h.start_sec;
  const trimmed = Math.abs(endSec - h.end_sec) >= 0.5;

  return (
  <div className="card hl-card">
    <div className="row" style={{ alignItems: "flex-start", gap: 16 }}>
      {videoUrl && (
        <SegmentPlayer
          videoUrl={videoUrl}
          startSec={h.start_sec}
          endSec={endSec}
          hookStartSec={h.hook_start_sec}
          hookEndSec={h.hook_end_sec}
          onSetEnd={changeEnd}
        />
      )}
      <div style={{ flex: 1, minWidth: 260 }}>
        <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
          <div>
            <strong>
              {rank}. {h.title}
            </strong>
            <div className="hint mono">
              {h.start} → {fmtClock(endSec)} · {Math.round(duration)}s (hook {h.hook_start} →{" "}
              {h.hook_end})
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
        <div className="end-trim">
          <label className="field" style={{ flex: 1, minWidth: 220 }}>
            <span style={{ display: "flex", justifyContent: "space-between" }}>
              <span>
                ends at <span className="mono">{fmtClock(endSec)}</span>
                {trimmed && <span className="hint"> (was {h.end})</span>}
              </span>
              <span
                className="mono"
                style={{ color: duration > maxDuration ? "var(--accent)" : undefined }}
              >
                {fmtTime(duration)}
              </span>
            </span>
            <input
              type="range"
              min={minEnd}
              max={maxEnd}
              step={0.5}
              value={endSec}
              onChange={(e) => changeEnd(Number(e.target.value))}
            />
          </label>
          <div className="row" style={{ gap: 4 }}>
            {[-5, -1, 1, 5].map((delta) => (
              <button key={delta} onClick={() => changeEnd(endSec + delta)}>
                {delta > 0 ? `+${delta}s` : `${delta}s`}
              </button>
            ))}
            <span className="hint" style={{ minWidth: 54 }}>
              {saveState === "saving" && "saving…"}
              {saveState === "saved" && "✓ saved"}
              {saveState === "error" && <span className="error">save failed</span>}
            </span>
          </div>
        </div>
        <div style={{ marginTop: 12 }}>
          <JobRunner
            kind="export_vertical"
            projectId={projectId}
            params={exportParams(h, rank, hookFirst, endSec)}
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
};

export const Highlights: React.FC<{
  project: ProjectState;
  onRefresh: () => Promise<void>;
  onNext: () => void;
}> = ({ project, onRefresh, onNext }) => {
  const [count, setCount] = useState(8);
  const [minDuration, setMinDuration] = useState(20);
  const [maxDuration, setMaxDuration] = useState(100);
  const [geminiModel, setGeminiModel] = useState("gemini-flash-latest");
  const [hookFirst, setHookFirst] = useState(true);
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
        1080×1920 file that opens with its hook, ready for your DaVinci edit — B-roll lands in the
        final vertical frame.
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
          <label
            className="field"
            style={{ flexDirection: "row", alignItems: "center", gap: 6, margin: "10px 0 4px" }}
          >
            <input
              type="checkbox"
              checked={hookFirst}
              onChange={(e) => setHookFirst(e.target.checked)}
            />
            open with the hook — cut the hook moment in front of the passage (it then repeats in
            context); turn off when the hook already is the opening line
          </label>
          {highlights.map((h, i) => (
            <HighlightCard
              key={`${h.start_sec}-${i}`}
              highlight={h}
              rank={i + 1}
              videoUrl={videoUrl}
              videoDuration={project.video.duration_sec ?? null}
              projectId={project.id}
              hookFirst={hookFirst}
              maxDuration={maxDuration}
              onExported={() => void loadHighlights()}
            />
          ))}
        </>
      )}
    </>
  );
};
