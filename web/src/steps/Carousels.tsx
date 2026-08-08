import { useEffect, useState } from "react";
import { api } from "../api";
import type { Carousel, ProjectState } from "../types";
import { JobRunner } from "../components/JobRunner";

/** Copy button that confirms itself for a moment instead of opening a toast. */
const CopyButton: React.FC<{ label: string; text: string }> = ({ label, text }) => {
  const [copied, setCopied] = useState(false);
  return (
    <button
      className="sm"
      onClick={() => {
        void navigator.clipboard.writeText(text).then(() => {
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1600);
        });
      }}
    >
      {copied ? "✓ Copied" : label}
    </button>
  );
};

/** the plain-text form of a carousel, for pasting into the design template */
const slidesAsText = (c: Carousel): string =>
  c.slides.map((slide, i) => `${i + 1}. ${slide}`).join("\n\n");

const captionAsText = (c: Carousel): string =>
  `${c.caption}\n\n${c.hashtags.map((tag) => `#${tag}`).join(" ")}`;

const CarouselCard: React.FC<{ carousel: Carousel; rank: number }> = ({ carousel: c, rank }) => (
  <div className="card hl-card">
    <div className="hl-head">
      <div style={{ minWidth: 0 }}>
        <h3>
          {rank}. {c.title}
        </h3>
        <div className="hint mono" style={{ marginTop: 3 }}>
          from {c.source_start} → {c.source_end} · {c.slides.length} frames
        </div>
      </div>
      <div className="score">
        <b>{c.save_score}</b>
        <span>saves</span>
      </div>
    </div>
    <div className="carousel-strip">
      {c.slides.map((slide, i) => (
        <div
          key={i}
          className={`carousel-frame${i === 0 ? " cover" : ""}${i === c.slides.length - 1 ? " last" : ""}`}
        >
          <span className="fnum">{i + 1}</span>
          <p>{slide}</p>
        </div>
      ))}
    </div>
    <p style={{ margin: "12px 0 0" }}>{c.caption}</p>
    <p className="hint" style={{ margin: "6px 0 0", overflowWrap: "anywhere" }}>
      {c.hashtags.map((tag) => `#${tag}`).join(" ")}
    </p>
    <p className="hint" style={{ margin: "8px 0 0" }}>
      {c.score_reason}
    </p>
    <div className="row" style={{ marginTop: 12 }}>
      <CopyButton label="Copy slide texts" text={slidesAsText(c)} />
      <CopyButton label="Copy caption + hashtags" text={captionAsText(c)} />
      <CopyButton label="Copy everything" text={`${c.title}\n\n${slidesAsText(c)}\n\n${captionAsText(c)}`} />
    </div>
  </div>
);

export const Carousels: React.FC<{
  project: ProjectState;
  onRefresh: () => Promise<void>;
}> = ({ project, onRefresh }) => {
  const [count, setCount] = useState(6);
  const [frames, setFrames] = useState(8);
  const [geminiModel, setGeminiModel] = useState("gemini-flash-latest");
  const [carousels, setCarousels] = useState<Carousel[] | null>(null);
  const [keyPresent, setKeyPresent] = useState(true);
  const done = project.steps.carousels === "done";
  // off the project state, not local — onRefresh after a run makes it appear
  const mdPath = project.artifacts.carousels_md?.exists ? project.artifacts.carousels_md.path : null;

  const loadCarousels = () =>
    api
      .carousels(project.id)
      .then((file) => setCarousels(file.carousels))
      .catch(() => setCarousels(null));

  useEffect(() => {
    if (done) void loadCarousels();
    api.geminiStatus().then(({ key_present }) => setKeyPresent(key_present));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project.id]);

  return (
    <>
      <h2>Carousels</h2>
      <p className="hint">
        The timestamped transcript (text only — no audio or video) goes to Gemini, which writes
        Instagram carousel posts out of the sermon's strongest ideas: a cover line that stops the
        scroll, one thought per frame, a closing takeaway, plus a caption and hashtags for the
        post. Pick how many carousels to suggest and how many frames each should have, then copy
        the texts into your design template. Everything is also saved next to the sermon in{" "}
        <code>00_SOURCE/</code> as JSON and Markdown.
      </p>
      {!keyPresent && (
        <p className="error">
          GEMINI_API_KEY is not set — add it to the .env file at the repo root and restart{" "}
          <code>sermon web</code>.
        </p>
      )}
      <div className="form-row">
        <label className="field">
          Carousels
          <input
            type="number"
            min={1}
            max={12}
            value={count}
            onChange={(e) => setCount(Number(e.target.value))}
            style={{ width: 72 }}
          />
        </label>
        <label className="field">
          Frames each
          <input
            type="number"
            min={3}
            max={20}
            value={frames}
            onChange={(e) => setFrames(Number(e.target.value))}
            style={{ width: 72 }}
          />
        </label>
        <label className="field">
          Gemini model
          <input value={geminiModel} onChange={(e) => setGeminiModel(e.target.value)} style={{ width: 190 }} />
        </label>
      </div>
      <JobRunner
        kind="carousels"
        projectId={project.id}
        params={{ count, frames, gemini_model: geminiModel }}
        label={done ? "Re-run carousels" : "Suggest carousels"}
        disabled={!keyPresent}
        onDone={(d) => {
          void onRefresh();
          if (d.state === "succeeded") void loadCarousels();
        }}
      />
      {carousels && (
        <div className="section">
          <div className="section-head">
            <h3>{carousels.length} suggestions</h3>
            <span className="row" style={{ gap: 8 }}>
              <span className="hint">sorted by save/share score</span>
              {mdPath && (
                <button className="sm" onClick={() => api.reveal(mdPath)}>
                  Reveal the Markdown
                </button>
              )}
            </span>
          </div>
          {carousels.map((c, i) => (
            <CarouselCard key={`${c.title}-${i}`} carousel={c} rank={i + 1} />
          ))}
        </div>
      )}
    </>
  );
};
