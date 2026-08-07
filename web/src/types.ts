export type Artifact = { path: string; exists: boolean; mtime: number | null };

/** per-clip caption style; `yOffset` lifts the caption block (px, negative = lower),
 *  a non-empty `speakerName` turns on the intro graphics (bottom blur, name, logo) */
export type CaptionStyle = { yOffset: number; speakerName?: string };

export type ClipState = {
  id: string;
  path: string;
  name: string;
  exists: boolean;
  /** the highlight folder this clip belongs to (`01_Boh ťa vidí`), null when outside the layout */
  highlight: string | null;
  /** the highlight's folder, or the clip's own when it sits outside the layout */
  folder: string;
  /** which stage folder it came out of: 01_VERTICAL_NO_CAPTION | 02_DAVINCI_EXPORT */
  stage: string | null;
  has_captions: boolean;
  has_framing: boolean;
  has_corrections: boolean;
  in_public: boolean;
  style?: CaptionStyle; // absent when an older server is still running
  /** splice points (sec) of a hook-first export — no caption page spans one */
  cuts?: number[];
  rendered: { path: string; exists: boolean; mtime: number | null; stale: boolean };
  urls: { video: string | null; rendered: string | null };
  duration_sec: number | null;
  width: number | null;
  height: number | null;
  fps: number | null;
};

export type StepStatus = "done" | "pending";

export type ProjectState = {
  id: string;
  video: {
    path: string;
    name: string;
    exists: boolean;
    /** the container needs re-wrapping before a browser or Resolve can open it (.mkv, .avi, …) */
    needs_conversion?: boolean;
    /** a playable copy is on disk — the source itself, or its remuxed 00_SOURCE twin */
    playable?: boolean;
    /** filename the preview URL ends in, so it carries an extension the browser knows */
    playable_name?: string;
    duration_sec?: number | null;
    width?: number | null;
    height?: number | null;
    fps?: number | null;
  };
  artifacts: Record<"transcript" | "srt" | "segments" | "highlights" | "highlights_md" | "resolve_xml", Artifact>;
  /** 00_SOURCE: everything derived from the sermon as a whole */
  source_dir: string;
  /** an explicit folder for finished renders; null = each one in its own highlight folder */
  output_dir: string | null;
  /** names off `_SPEAKERS.txt` in the sermon folder; the first pre-fills each clip's intro */
  speakers?: string[];
  clips: ClipState[];
  steps: Record<"transcribe" | "highlights" | "export" | "clips" | "captions" | "render", StepStatus>;
};

export type Segment = { start: number; end: number; text: string };
export type SegmentsFile = {
  video: string;
  language: string;
  model: string;
  duration: number;
  segments: Segment[];
};

export type Highlight = {
  start: string;
  end: string;
  start_sec: number;
  end_sec: number;
  duration_sec: number;
  hook_start: string;
  hook_end: string;
  hook_start_sec: number;
  hook_end_sec: number;
  hook_duration_sec: number;
  title: string;
  summary: string;
  hook_text: string;
  virality_score: number;
  scores: { hook: number; flow: number; value: number; reach: number };
  score_reason: string;
  excerpt: string;
  /** server-augmented: the vertical tracked export for this highlight */
  vertical?: { index: number; path: string; exists: boolean };
  /** server-augmented: this highlight's own folder, `<dir>/01_<title>` */
  folder?: string;
  /** server-augmented: where the DaVinci render is expected */
  davinci_dir?: string;
  /** server-augmented: the finished captioned video */
  final?: { path: string; exists: boolean };
};
export type HighlightsFile = { video: string; gemini_model: string; highlights: Highlight[] };

export type Caption = {
  text: string;
  startMs: number;
  endMs: number;
  timestampMs: number | null;
  confidence: number | null;
};

export type Framing = {
  sourceWidth: number;
  sourceHeight: number;
  keyframes: { t: number; cx: number }[];
  cuts?: number[];
  duration?: number;
};

export type FsEntry = { name: string; path: string; is_dir: boolean; size: number | null; mtime: number };
export type FsListing = { path: string; parent: string | null; entries: FsEntry[] };

export type JobProgress = { percent?: number | null; stage?: string; detail?: string };
export type JobDone = {
  state: "succeeded" | "failed" | "canceled";
  exit_code: number | null;
  result: Record<string, unknown> | null;
  error: string | null;
};
export type JobKind =
  | "convert"
  | "transcribe"
  | "highlights"
  | "export_vertical"
  | "captions"
  | "track"
  | "render";

export type Correction = { index: number; before: string; after: string; reason: string };
export type CorrectionsFile = { gemini_model: string; corrections: Correction[] };

export type JobSnapshot = {
  id: string;
  kind: JobKind;
  project_id: string | null;
  clip_id: string | null;
  state: "running" | "succeeded" | "failed" | "canceled";
  /** cancel was requested and the job's processes are still being taken down */
  canceling?: boolean;
  progress: JobProgress;
  result: Record<string, unknown> | null;
  error: string | null;
  exit_code: number | null;
  log_tail: string[];
};
