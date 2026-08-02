import { useEffect, useRef } from "react";

export type LogLine = { line: string; stream: string };

export const LogPane: React.FC<{ lines: LogLine[] }> = ({ lines }) => {
  const ref = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);

  useEffect(() => {
    const el = ref.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [lines]);

  return (
    <div
      className="log-pane"
      ref={ref}
      onScroll={(e) => {
        const el = e.currentTarget;
        pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
      }}
    >
      {lines.map((l, i) => (
        <div key={i} className={l.stream === "err" ? "err" : undefined}>
          {l.line}
        </div>
      ))}
    </div>
  );
};
