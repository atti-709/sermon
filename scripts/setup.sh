#!/usr/bin/env bash
# Install (or re-install) everything the sermon pipeline needs. Safe to re-run.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

say() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
die() { printf '\033[31merror: %s\033[0m\n' "$1" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || die "macOS only — transcription, tracking and encoding use Apple frameworks"
[ "$(uname -m)" = "arm64" ] || die "Apple Silicon required — mlx-whisper runs on the Metal GPU"

missing=()
for tool in ffmpeg node uv; do
  command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
done
if [ ${#missing[@]} -gt 0 ]; then
  command -v brew >/dev/null 2>&1 || die "missing ${missing[*]} — install Homebrew first: https://brew.sh"
  say "Installing ${missing[*]} with Homebrew"
  brew install "${missing[@]}"
fi

node_major="$(node -p 'process.versions.node.split(".")[0]')"
[ "$node_major" -ge 18 ] || die "Node 18+ required for Remotion (found $(node -v)) — run: brew upgrade node"

say "Python dependencies"
uv sync

say "Remotion renderer (captions/)"
(cd captions && npm install --no-fund --no-audit)

say "Web UI (web/)"
(cd web && npm install --no-fund --no-audit && npm run build)

if [ ! -f .env ]; then
  cp .env.example .env
fi

say "Done"
if ! grep -qE '^GEMINI_API_KEY=.+' .env; then
  printf 'Add your Gemini API key to .env before the highlights step:\n'
  printf '  GEMINI_API_KEY=...   (free key: https://aistudio.google.com/apikey)\n\n'
fi
printf 'Start the app with:\n  uv run sermon web\n'
