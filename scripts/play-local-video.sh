#!/usr/bin/env bash
# Play a local media file fullscreen with mpv. Repo root is derived from this script.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
REL="${1:-}"
if [[ -z "${REL}" ]]; then
  echo "usage: play-local-video.sh <file-under-media/>|</absolute/path>" >&2
  exit 2
fi
if [[ "${REL}" == /* ]]; then
  FILE="${REL}"
else
  FILE="${REPO_ROOT}/media/${REL}"
fi
if [[ ! -f "${FILE}" ]]; then
  echo "play-local-video: not found: ${FILE}" >&2
  exit 1
fi
exec mpv --fs --no-terminal --really-quiet "${FILE}"
