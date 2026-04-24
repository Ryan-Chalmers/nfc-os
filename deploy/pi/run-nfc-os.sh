#!/usr/bin/env bash
# Run NFC OS Qt shell under an existing X11 session (e.g. openbox started via startx).
set -euo pipefail

ROOT="${NFC_OS_HOME:-/opt/nfc-os}"
export PYTHONPATH="${ROOT}/src"
export DISPLAY="${DISPLAY:-:0}"

cd "${ROOT}"
exec "${ROOT}/.venv/bin/python" "${ROOT}/main.py" "$@"
