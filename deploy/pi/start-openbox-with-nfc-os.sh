#!/usr/bin/env bash
# Thin wrapper for a custom X11 session entry. Prefer configuring Openbox autostart
# to run run-nfc-os.sh so the window manager owns the session lifecycle (see README).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export NFC_OS_HOME="${NFC_OS_HOME:-$ROOT}"
exec "${NFC_OS_HOME}/deploy/pi/run-nfc-os.sh" "$@"
