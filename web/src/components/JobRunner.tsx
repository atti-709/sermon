import { useCallback, useEffect, useRef, useState } from "react";
import { api, subscribeToJob } from "../api";
import type { JobDone, JobKind, JobProgress } from "../types";
import { LogLine, LogPane } from "./LogPane";

/** Start button + progress bar + streaming log for one pipeline job.
 *  On mount it checks the server for a matching job that is already running
 *  (started before a tab switch) and re-attaches to it, replaying the log. */
export const JobRunner: React.FC<{
  kind: JobKind;
  projectId: string;
  clipId?: string;
  params?: Record<string, unknown>;
  label: string;
  /** start immediately on mount (used to auto-chain tracking after captions) */
  autoStart?: boolean;
  disabled?: boolean;
  onDone?: (done: JobDone) => void;
}> = ({ kind, projectId, clipId, params, label, autoStart, disabled, onDone }) => {
  const [running, setRunning] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [progress, setProgress] = useState<JobProgress | null>(null);
  const [lines, setLines] = useState<LogLine[]>([]);
  const [error, setError] = useState<string | null>(null);
  const unsubscribe = useRef<(() => void) | null>(null);
  const onDoneRef = useRef(onDone);
  onDoneRef.current = onDone;

  const attach = useCallback((job_id: string) => {
    setRunning(true);
    setJobId(job_id);
    unsubscribe.current?.();
    unsubscribe.current = subscribeToJob(job_id, {
      onLog: (line, stream) => setLines((prev) => [...prev.slice(-1500), { line, stream }]),
      onProgress: setProgress,
      onDone: (done) => {
        setRunning(false);
        setJobId(null);
        if (done.state === "failed") setError(done.error ?? `job failed (exit ${done.exit_code})`);
        onDoneRef.current?.(done);
      },
    });
  }, []);

  const start = useCallback(async () => {
    setError(null);
    setLines([]);
    setProgress(null);
    setRunning(true);
    try {
      const { job_id } = await api.startJob({ kind, project_id: projectId, clip_id: clipId, params });
      attach(job_id);
    } catch (exc) {
      setRunning(false);
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }, [kind, projectId, clipId, params, attach]);

  const startedOnMount = useRef(false);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { job } = await api.currentJob();
        if (
          !cancelled &&
          job &&
          job.state === "running" &&
          job.kind === kind &&
          job.project_id === projectId &&
          (job.clip_id ?? null) === (clipId ?? null)
        ) {
          attach(job.id); // job kept running while this tab was away — pick it back up
          return;
        }
      } catch {
        /* server unreachable — the start button still works */
      }
      if (!cancelled && autoStart && !startedOnMount.current) {
        startedOnMount.current = true;
        void start();
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => () => unsubscribe.current?.(), []);

  const percent = progress?.percent ?? null;
  return (
    <div>
      {!running ? (
        <button className="primary" onClick={start} disabled={disabled}>
          {label}
        </button>
      ) : (
        <>
          <div className="row" style={{ gap: 12, minHeight: "var(--control-h)" }}>
            <div
              className="progress-track"
              role="progressbar"
              aria-label={label}
              aria-valuenow={percent != null ? Math.round(percent) : undefined}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <div
                className={`progress-fill${percent == null ? " indeterminate" : ""}`}
                style={{ width: `${percent ?? 0}%` }}
              />
            </div>
            <span className="hint percent">{percent != null ? `${Math.round(percent)}%` : "…"}</span>
            <button className="sm" onClick={() => jobId && api.cancelJob(jobId)}>
              Cancel
            </button>
          </div>
          {progress?.detail && (
            <p className="hint mono" style={{ margin: "8px 0 0" }}>
              {progress.detail}
            </p>
          )}
        </>
      )}
      {(running || lines.length > 0) && (
        <div style={{ marginTop: 12 }}>
          <LogPane lines={lines} />
        </div>
      )}
      {error && <p className="error">{error}</p>}
    </div>
  );
};
