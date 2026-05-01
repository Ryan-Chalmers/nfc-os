#!/usr/bin/env bash
# Run NFC OS Qt shell under an existing X11 session (e.g. openbox started via startx).
set -euo pipefail

ROOT="${NFC_OS_HOME:-/opt/nfc-os}"
export PYTHONPATH="${ROOT}/src"
export DISPLAY="${DISPLAY:-:0}"
# Pi / kiosk: URL cartridges default to the system browser (xdg-open); Chromium flags
# apply only if you force embedded WebEngine with NFC_OS_URL_EMBEDDED=1.
export QTWEBENGINE_CHROMIUM_FLAGS="${QTWEBENGINE_CHROMIUM_FLAGS:---disable-gpu --disable-gpu-compositing --no-sandbox --disable-dev-shm-usage}"

cd "${ROOT}"
exec "${ROOT}/.venv/bin/python" "${ROOT}/main.py" "$@"
