#!/usr/bin/env bash
# Pull the latest version and re-install anything that changed.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

git pull --ff-only
exec scripts/setup.sh
