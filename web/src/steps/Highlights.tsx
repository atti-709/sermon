import { useEffect, useRef, useState } from "react";
import { api, fmtTime } from "../api";
import type { Highlight, ProjectState } from "../types";
import { JobRunner } from "../components/JobRunner";
import { SegmentPlayer } from "../components/SegmentPlayer";

/** how far the out point may be dragged, as seconds from the highlight's start */
const MIN_CLIP_SEC = 10;
const MAX_CLIP_SEC = 200;

/** who the 9:16 crop follows: the biggest face, one picked person, or whoever talks */
type Framing = { subjectX: number | null; followSpeaker: boolean };

/** HH:MM:SS, matching the timestamps the highlights file carries */
const fmtClock = (sec: number): string => {
  const s = Math.max(0, Math.floor(sec));
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(Math.floor(s / 3600))}:${pad(Math.floor((s % 3600) / 60))}:${pad(s % 60)}`;
};

/** Export range for one highlight: hook-first cuts the hook moment in front of the
 *  passage, otherwise the passage is widened to cover a hook that falls outside it.
 *  `framing` says who the crop follows when the frame holds more than one person. */
const exportParams = (
  h: Highlight,
  rank: number,
  hookFirst: boolean,
  endSec: number,
  framing: Framing,
) => {
  const hasHook = h.hook_start_sec != null && h.hook_end_sec != null;
  const subject =
    framing.subjectX != null
      ? { subject_x: framing.subjectX }
      : framing.followSpeaker
        ? { follow_speaker: true }
        : {};
  if (hookFirst && hasHook) {
    return {
      start_sec: h.start_sec,
      end_sec: endSec,
      hook_start_sec: h.hook_start_sec,
      hook_end_sec: h.hook_end_sec,
      index: rank,
      ...subject,
    };
  }
  return {
    start_sec: Math.min(h.start_sec, h.hook_start_sec ?? h.start_sec),
    end_sec: Math.max(endSec, h.hook_end_sec ?? endSec),
    index: rank,
    ...subject,
  };
};

const HighlightCard: React.FC<{
  highlight: Highlight;
  rank: number;
  videoUrl: string | null;
  videoDuration: number | null;
  videoAspect: number | null;
  projectId: string;
  hookFirst: boolean;
  maxDuration: number;
  onExported: () => void;
}> = ({
  highlight: h,
  rank,
  videoUrl,
  videoDuration,
  videoAspect,
  projectId,
  hookFirst,
  maxDuration,
  onExported,
}) => {
  // the out point and the framing are edited here and saved back into the highlights
  // file, so the export, the Resolve XML and the notes all follow them
  const [endSec, setEndSec] = useState(h.end_sec);
  const [framing, setFraming] = useState<Framing>({
    subjectX: h.subject_x ?? null,
    followSpeaker: h.follow_speaker === true,
  });
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
        .patchHighlight(projectId, rank, { end_sec: clamped })
        .then((updated) => {
          setEndSec(updated.end_sec); // the server clamps to the video's length
          setSaveState("saved");
        })
        .catch(() => setSaveState("error"));
    }, 400);
  };

  // one click, so it saves straight away rather than on a debounce like the slider.
  // The two modes are alternatives: the server drops whichever one is not being set,
  // and the local state mirrors that so the buttons agree with what was saved.
  const changeFraming = (patch: { subject_x?: number | null; follow_speaker?: boolean }) => {
    setFraming({
      subjectX: patch.subject_x ?? null,
      followSpeaker: patch.subject_x == null && patch.follow_speaker === true,
    });
    setSaveState("saving");
    api
      .patchHighlight(projectId, rank, patch)
      .then(() => setSaveState("saved"))
      .catch(() => setSaveState("error"));
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
          sourceAspect={videoAspect}
          subjectX={framing.subjectX}
          followSpeaker={framing.followSpeaker}
          onFraming={changeFraming}
        />
      )}
      <div style={{ flex: 1, minWidth: 260 }}>
        <div className="hl-head">
          <div style={{ minWidth: 0 }}>
            <h3>
              {rank}. {h.title}
            </h3>
            <div className="hint mono" style={{ marginTop: 3 }}>
              {h.start} → {fmtClock(endSec)} · {Math.round(duration)}s · hook {h.hook_start} →{" "}
              {h.hook_end}
            </div>
          </div>
          <div className="score">
            <b>{h.virality_score}</b>
            <span>virality</span>
          </div>
        </div>
        <div className="subscores">
          <span>
            <b>{h.scores.hook}</b> hook
          </span>
          <span>
            <b>{h.scores.flow}</b> flow
          </span>
          <span>
            <b>{h.scores.value}</b> value
          </span>
          <span>
            <b>{h.scores.reach}</b> reach
          </span>
        </div>
        <div className="hook">“{h.hook_text}”</div>
        <p style={{ margin: "10px 0 0" }}>{h.summary}</p>
        <details>
          <summary>Excerpt & scoring</summary>
          <p>{h.excerpt}</p>
          <p className="hint">{h.score_reason}</p>
        </details>
        <div className="end-trim">
          <label className="field">
            <span>
              <span>
                Ends at <span className="mono">{fmtClock(endSec)}</span>
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
          <div className="seg">
            {[-5, -1, 1, 5].map((delta) => (
              <button
                key={delta}
                className="sm"
                aria-label={`move the out point ${delta > 0 ? `${delta} seconds later` : `${-delta} seconds earlier`}`}
                onClick={() => changeEnd(endSec + delta)}
              >
                {delta > 0 ? `+${delta}s` : `${delta}s`}
              </button>
            ))}
          </div>
          <span className="save-state" role="status">
            {saveState === "saving" && "Saving…"}
            {saveState === "saved" && "✓ Saved"}
            {saveState === "error" && <span className="error">Save failed</span>}
          </span>
        </div>
        <div style={{ marginTop: 14 }}>
          <JobRunner
            kind="export_vertical"
            projectId={projectId}
            params={exportParams(h, rank, hookFirst, endSec, framing)}
            label={h.vertical?.exists ? "Re-export vertical" : "Export vertical for DaVinci"}
            onDone={(done) => {
              if (done.state === "succeeded") onExported();
            }}
          />
          {h.vertical?.exists && (
            <>
              <div className="row" style={{ marginTop: 12, justifyContent: "space-between" }}>
                <span className="chip on">vertical · tracked · 1080×1920</span>
                <span className="row" style={{ gap: 8 }}>
                  {h.folder && (
                    <button className="sm" onClick={() => api.reveal(h.folder!)}>
                      Open this highlight's folder
                    </button>
                  )}
                  <button className="sm" onClick={() => api.reveal(h.vertical!.path)}>
                    Reveal the clip
                  </button>
                </span>
              </div>
              <div className="path" style={{ marginTop: 6 }}>
                {h.folder ? `${h.folder.split("/").pop()}/01_VERTICAL_NO_CAPTION/${h.vertical.path.split("/").pop()}` : h.vertical.path}
              </div>
              {h.final?.exists && <div className="chip on" style={{ marginTop: 8 }}>finished video ready</div>}
            </>
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
}> = ({ project, onRefresh }) => {
  const [count, setCount] = useState(8);
  const [minDuration, setMinDuration] = useState(20);
  const [maxDuration, setMaxDuration] = useState(100);
  const [geminiModel, setGeminiModel] = useState("gemini-flash-latest");
  const [hookFirst, setHookFirst] = useState(true);
  const [highlights, setHighlights] = useState<Highlight[] | null>(null);
  const [keyPresent, setKeyPresent] = useState(true);
  const done = project.steps.highlights === "done";

  // a container the browser refuses (.mkv and friends) is previewed through the
  // remuxed twin in 00_SOURCE, which the server resolves from the project id
  const needsConversion = project.video.needs_conversion === true;
  const playable = project.video.playable !== false;
  // the URL ends in the playable file's own name: players and <video> read the
  // media type off the path, so an extensionless URL is asking for trouble even
  // when the Content-Type is right
  const playableName = project.video.playable_name ?? project.video.name;
  const videoUrl =
    project.video.exists && playable
      ? `/media/source/${project.id}/${encodeURIComponent(playableName)}`
      : null;
  const { width, height } = project.video;
  const videoAspect = width && height ? width / height : null;

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
        final vertical frame. When several people share the frame — a panel, an interview — the
        buttons under a preview say who the crop belongs to: <strong>One person</strong> you click,
        or <strong>Whoever speaks</strong>, which reads lip movement against the soundtrack and
        cuts between them. Each export gets a folder of its own next to the sermon —{" "}
        <code>01_Title/01_VERTICAL_NO_CAPTION/</code> — and the finished video lands in that
        folder's root at the end.
      </p>
      {!keyPresent && (
        <p className="error">
          GEMINI_API_KEY is not set — add it to the .env file at the repo root and restart{" "}
          <code>sermon web</code>.
        </p>
      )}
      {needsConversion && !playable && (
        <div className="card">
          <strong>{project.video.name} can't be played here yet</strong>
          <p className="hint" style={{ margin: "6px 0 12px" }}>
            Transcription and the vertical export read this file fine, but neither a browser nor
            DaVinci Resolve opens its container — so the previews below and the Resolve timeline need
            an <code>.mp4</code> copy in <code>00_SOURCE/</code> first. The video itself is normally
            copied across untouched, without being re-encoded, which takes seconds and costs no
            quality; only a codec the browser can't decode at all forces a real re-encode. The
            sermon file stays exactly where it is.
          </p>
          <JobRunner
            kind="convert"
            projectId={project.id}
            label="Make a playable copy"
            onDone={(d) => {
              if (d.state === "succeeded") void onRefresh();
            }}
          />
        </div>
      )}
      <div className="form-row">
        <label className="field">
          Clips
          <input
            type="number"
            min={1}
            max={20}
            value={count}
            onChange={(e) => setCount(Number(e.target.value))}
            style={{ width: 72 }}
          />
        </label>
        <label className="field">
          Shortest (s)
          <input
            type="number"
            value={minDuration}
            onChange={(e) => setMinDuration(Number(e.target.value))}
            style={{ width: 82 }}
          />
        </label>
        <label className="field">
          Longest (s)
          <input
            type="number"
            value={maxDuration}
            onChange={(e) => setMaxDuration(Number(e.target.value))}
            style={{ width: 82 }}
          />
        </label>
        <label className="field">
          Gemini model
          <input value={geminiModel} onChange={(e) => setGeminiModel(e.target.value)} style={{ width: 190 }} />
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
        <div className="section">
          <div className="section-head">
            <h3>{highlights.length} suggestions</h3>
            <span className="hint">sorted by virality</span>
          </div>
          <label className="check" style={{ marginBottom: 14 }}>
            <input
              type="checkbox"
              checked={hookFirst}
              onChange={(e) => setHookFirst(e.target.checked)}
            />
            <span>
              Open every export with its hook
              <span className="check-hint">
                Cuts the hook moment in front of the passage, where it then repeats in context. Turn
                off when the hook already is the opening line.
              </span>
            </span>
          </label>
          {highlights.map((h, i) => (
            <HighlightCard
              key={`${h.start_sec}-${i}`}
              highlight={h}
              rank={i + 1}
              videoUrl={videoUrl}
              videoDuration={project.video.duration_sec ?? null}
              videoAspect={videoAspect}
              projectId={project.id}
              hookFirst={hookFirst}
              maxDuration={maxDuration}
              onExported={() => void loadHighlights()}
            />
          ))}
        </div>
      )}
    </>
  );
};
