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
            <button onClick={() => load(listing.parent!)}>
              <span aria-hidden="true">⬑</span>
              <span className="fb-name">Up one folder</span>
            </button>
          </li>
        )}
        {listing.entries.map((entry) => (
          <li key={entry.path}>
            <button onClick={() => (entry.is_dir ? load(entry.path) : onPick(entry.path))}>
              <span aria-hidden="true">{entry.is_dir ? "📁" : "🎬"}</span>
              <span className="fb-name">{entry.name}</span>
              {entry.size != null && <span className="size">{fmtSize(entry.size)}</span>}
            </button>
          </li>
        ))}
        {listing.entries.length === 0 && (
          <li>
            <p className="hint" style={{ margin: 0, padding: "8px 10px" }}>
              No videos in this folder. Open a subfolder or go up one level.
            </p>
          </li>
        )}
      </ul>
    </div>
  );
};
