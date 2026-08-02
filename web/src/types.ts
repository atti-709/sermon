export type Artifact = { path: string; exists: boolean; mtime: number | null };

export type ClipState = {
  id: string;
  path: string;
  name: string;
  exists: boolean;
  has_captions: boolean;
  has_framing: boolean;
  has_corrections: boolean;
  in_public: boolean;
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
    duration_sec?: number | null;
    width?: number | null;
    height?: number | null;
    fps?: number | null;
  };
  artifacts: Record<"transcript" | "srt" | "segments" | "highlights" | "highlights_md" | "resolve_xml", Artifact>;
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
export type JobKind = "transcribe" | "highlights" | "export_vertical" | "captions" | "track" | "render";

export type Correction = { index: number; before: string; after: string; reason: string };
export type CorrectionsFile = { gemini_model: string; corrections: Correction[] };

export type JobSnapshot = {
  id: string;
  kind: JobKind;
  project_id: string | null;
  clip_id: string | null;
  state: "running" | "succeeded" | "failed" | "canceled";
  progress: JobProgress;
  result: Record<string, unknown> | null;
  error: string | null;
  exit_code: number | null;
  log_tail: string[];
};
