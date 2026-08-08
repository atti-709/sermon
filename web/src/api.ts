import type {
  Caption,
  CaptionStyle,
  CarouselsFile,
  ClipState,
  CorrectionsFile,
  Framing,
  FsListing,
  Highlight,
  HighlightsFile,
  JobDone,
  JobKind,
  JobProgress,
  JobSnapshot,
  ProjectState,
  SegmentsFile,
} from "./types";

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body.detail) detail = String(body.detail);
    } catch {
      /* keep the status text */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

const post = <T,>(url: string, body: unknown): Promise<T> =>
  request<T>(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

export const api = {
  geminiStatus: () => request<{ key_present: boolean }>("/api/gemini-status"),
  fsList: (path?: string) =>
    request<FsListing>(`/api/fs/list${path ? `?path=${encodeURIComponent(path)}` : ""}`),
  /** native macOS dialog; `startPath` opens it in a folder (a highlight's 02_DAVINCI_EXPORT) */
  pickFile: (kind: "video" | "clip" | "folder", startPath?: string) =>
    post<{ path: string | null }>("/api/fs/pick", { kind, start_path: startPath ?? null }),
  createProject: (videoPath: string) => post<ProjectState>("/api/projects", { video_path: videoPath }),
  listProjects: () => request<{ projects: ProjectState[] }>("/api/projects"),
  getProject: (id: string) => request<ProjectState>(`/api/projects/${id}`),
  transcript: (id: string) => request<SegmentsFile>(`/api/projects/${id}/transcript`),
  highlights: (id: string) => request<HighlightsFile>(`/api/projects/${id}/highlights`),
  carousels: (id: string) => request<CarouselsFile>(`/api/projects/${id}/carousels`),
  /** edit one highlight (1-based rank) — rewrites the highlights file, so the export,
   *  the Resolve XML and the notes all follow. `subject_x: null` clears the pick. */
  patchHighlight: (
    id: string,
    index: number,
    patch: { end_sec?: number; subject_x?: number | null; follow_speaker?: boolean },
  ) =>
    request<Highlight>(`/api/projects/${id}/highlights/${index}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }),
  setOutputDir: (id: string, path: string) =>
    post<{ output_dir: string }>(`/api/projects/${id}/output-dir`, { path }),
  /** back to the default: each finished video in its own highlight folder */
  clearOutputDir: (id: string) =>
    request<{ output_dir: null }>(`/api/projects/${id}/output-dir`, { method: "DELETE" }),
  exportXml: (id: string) => post<{ xml_path: string }>(`/api/projects/${id}/export`, {}),
  addClip: (id: string, clipPath: string) =>
    post<ClipState>(`/api/projects/${id}/clips`, { clip_path: clipPath }),
  getCaptions: (id: string, clipId: string) =>
    request<Caption[]>(`/api/projects/${id}/clips/${clipId}/captions`),
  putCaptions: (id: string, clipId: string, captions: Caption[]) =>
    request<{ ok: boolean }>(`/api/projects/${id}/clips/${clipId}/captions`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(captions),
    }),
  getStyle: (id: string, clipId: string) =>
    request<CaptionStyle>(`/api/projects/${id}/clips/${clipId}/style`),
  putStyle: (id: string, clipId: string, style: CaptionStyle) =>
    request<{ ok: boolean; style: CaptionStyle }>(`/api/projects/${id}/clips/${clipId}/style`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(style),
    }),
  getFraming: (id: string, clipId: string) =>
    request<Framing>(`/api/projects/${id}/clips/${clipId}/framing`),
  getCorrections: (id: string, clipId: string) =>
    request<CorrectionsFile>(`/api/projects/${id}/clips/${clipId}/corrections`),
  currentJob: () => request<{ job: JobSnapshot | null }>("/api/jobs/current"),
  startJob: (req: { kind: JobKind; project_id?: string; clip_id?: string; params?: Record<string, unknown> }) =>
    post<{ job_id: string }>("/api/jobs", req),
  cancelJob: (jobId: string) => post<{ ok: boolean }>(`/api/jobs/${jobId}/cancel`, {}),
  reveal: (path: string) => post<{ ok: boolean }>("/api/reveal", { path }),
};

export type JobHandlers = {
  onLog?: (line: string, stream: string) => void;
  onProgress?: (progress: JobProgress) => void;
  onDone?: (done: JobDone) => void;
};

/** Subscribe to a job's SSE stream; returns an unsubscribe function. */
export function subscribeToJob(jobId: string, handlers: JobHandlers): () => void {
  const source = new EventSource(`/api/jobs/${jobId}/events`);
  source.addEventListener("log", (e) => {
    const payload = JSON.parse((e as MessageEvent).data);
    handlers.onLog?.(payload.line, payload.stream);
  });
  source.addEventListener("progress", (e) => {
    handlers.onProgress?.(JSON.parse((e as MessageEvent).data));
  });
  source.addEventListener("done", (e) => {
    handlers.onDone?.(JSON.parse((e as MessageEvent).data));
    source.close();
  });
  return () => source.close();
}

export const fmtTime = (sec: number): string => {
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  return h > 0
    ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
    : `${m}:${String(s).padStart(2, "0")}`;
};
