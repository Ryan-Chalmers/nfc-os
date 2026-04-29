#!/usr/bin/env bash
# Stop nfc-os entrypoints (python ... main.py) that belong to this repo only.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
sig="${1:-TERM}"
if [[ "$sig" != "TERM" && "$sig" != "KILL" && "$sig" != "INT" ]]; then
  echo "usage: $0 [TERM|KILL|INT]  (default: TERM)" >&2
  exit 2
fi
killed=()
for pid in $(pgrep -f 'main[.]py' || true); do
  [[ -r "/proc/${pid}/cmdline" ]] || continue
  if tr '\0' ' ' < "/proc/${pid}/cmdline" | grep -qF "$REPO_ROOT"; then
    kill "-${sig}" "$pid" 2>/dev/null && killed+=("$pid") || true
  fi
done
if ((${#killed[@]})); then
  echo "sent ${sig} to PIDs: ${killed[*]}"
else
  echo "no nfc-os main.py process found under ${REPO_ROOT}" >&2
fi
