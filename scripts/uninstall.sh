#!/usr/bin/env bash
# Undo scripts/setup.sh on a machine that no longer needs the pipeline:
# remove the installed libraries, downloaded speech models, and the Homebrew
# tools (ffmpeg, node, uv). Rendered videos and the project folder stay put.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

say() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
die() { printf '\033[31merror: %s\033[0m\n' "$1" >&2; exit 1; }
size_of() { du -sh "$1" 2>/dev/null | cut -f1; }

assume_yes=0
case "${1:-}" in
  -y|--yes) assume_yes=1 ;;
  "") ;;
  *) die "unknown option: $1 (only -y/--yes is supported)" ;;
esac

# Project-local artifacts created by setup.sh
project_paths=()
for p in .venv captions/node_modules web/node_modules web/dist .env; do
  if [ -e "$p" ]; then project_paths+=("$p"); fi
done

# Model/tool caches created the first time the pipeline runs
cache_paths=()
for p in "${HF_HOME:-$HOME/.cache/huggingface}" "$HOME/.cache/torch"; do
  if [ -e "$p" ]; then cache_paths+=("$p"); fi
done

# Homebrew tools — only ones actually installed, and only if nothing else
# on this machine depends on them
remove_tools=()
kept_tools=()
if command -v brew >/dev/null 2>&1; then
  for tool in ffmpeg node uv; do
    if [ -n "$(brew list --formula --versions "$tool" 2>/dev/null)" ]; then
      dependents="$(brew uses --installed "$tool" 2>/dev/null | tr '\n' ' ')"
      dependents="${dependents% }"
      if [ -n "$dependents" ]; then
        kept_tools+=("$tool (needed by: $dependents)")
      else
        remove_tools+=("$tool")
      fi
    fi
  done
fi

if [ ${#project_paths[@]} -eq 0 ] && [ ${#cache_paths[@]} -eq 0 ] && [ ${#remove_tools[@]} -eq 0 ]; then
  printf 'Nothing to remove — this machine is already clean.\n'
  exit 0
fi

printf 'This will remove:\n'
if [ ${#project_paths[@]} -gt 0 ]; then
  for p in "${project_paths[@]}"; do
    printf '  %-24s %s\n' "$p" "$(size_of "$p")"
  done
fi
if [ ${#cache_paths[@]} -gt 0 ]; then
  for p in "${cache_paths[@]}"; do
    printf '  %-24s %s (downloaded speech models)\n' "$p" "$(size_of "$p")"
  done
fi
if [ ${#remove_tools[@]} -gt 0 ]; then
  printf '  Homebrew tools: %s (plus their unused dependencies)\n' "${remove_tools[*]}"
fi
if [ ${#kept_tools[@]} -gt 0 ]; then
  printf 'Kept because other software needs them:\n'
  for t in "${kept_tools[@]}"; do printf '  %s\n' "$t"; done
fi
printf 'Rendered videos and the project folder itself are not touched.\n'

if [ "$assume_yes" -ne 1 ]; then
  [ -t 0 ] || die "not running interactively — re-run with --yes to confirm removal"
  printf '\nProceed? [y/N] '
  read -r answer
  case "$answer" in
    y|Y|yes|YES) ;;
    *) printf 'Nothing removed.\n'; exit 0 ;;
  esac
fi

if [ ${#project_paths[@]} -gt 0 ]; then
  say "Project libraries"
  rm -rf "${project_paths[@]}"
fi
find . -type d \( -name __pycache__ -o -name '*.egg-info' \) -prune -exec rm -rf {} + 2>/dev/null || true

if [ ${#cache_paths[@]} -gt 0 ]; then
  say "Downloaded speech models"
  rm -rf "${cache_paths[@]}"
fi

if command -v uv >/dev/null 2>&1; then
  say "uv cache and managed Python versions"
  uv cache clean >/dev/null 2>&1 || true
  uv python uninstall --all >/dev/null 2>&1 || true
fi
rm -rf "$HOME/.local/share/uv" "$HOME/.cache/uv" "$HOME/Library/Caches/uv"

if [ ${#remove_tools[@]} -gt 0 ]; then
  say "Homebrew tools: ${remove_tools[*]}"
  brew uninstall "${remove_tools[@]}"
  say "Unused Homebrew dependencies"
  brew autoremove
  brew cleanup --prune=all >/dev/null 2>&1 || true
fi
case " ${remove_tools[*]-} " in
  *" node "*) rm -rf "$HOME/.npm" ;;
esac

say "Done"
printf 'To finish, move this folder to the Trash:\n  %s\n' "$(pwd)"
