import { useEffect, useState } from "react";
import { api } from "../api";
import type { FsListing } from "../types";

const fmtSize = (bytes: number): string =>
  bytes > 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : `${Math.round(bytes / 1e6)} MB`;

export const FileBrowser: React.FC<{
  startPath?: string;
  onPick: (path: string) => void;
}> = ({ startPath, onPick }) => {
  const [listing, setListing] = useState<FsListing | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = (path?: string) =>
    api
      .fsList(path)
      .then((l) => {
        setListing(l);
        setError(null);
      })
      .catch((exc) => setError(exc.message));

  useEffect(() => {
    void load(startPath);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (error) return <p className="error">{error}</p>;
  if (!listing) return <p className="hint">Loading…</p>;

  return (
    <div className="file-browser">
      <div className="fb-path">{listing.path}</div>
      <ul>
        {listing.parent && (
          <li>
            <button onClick={() => load(listing.parent!)}>⬑ ..</button>
          </li>
        )}
        {listing.entries.map((entry) => (
          <li key={entry.path}>
            <button onClick={() => (entry.is_dir ? load(entry.path) : onPick(entry.path))}>
              <span>{entry.is_dir ? "📁" : "🎬"}</span>
              <span>{entry.name}</span>
              {entry.size != null && <span className="size">{fmtSize(entry.size)}</span>}
            </button>
          </li>
        ))}
        {listing.entries.length === 0 && (
          <li>
            <span className="hint" style={{ padding: "6px 10px", display: "block" }}>
              no videos here
            </span>
          </li>
        )}
      </ul>
    </div>
  );
};
