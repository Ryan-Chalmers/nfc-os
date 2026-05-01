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
  # Avoid matching a shell that is only preparing to exec `python ... main.py`
  # (e.g. `bash -c '... kill-nfc-os.sh; ... python -u main.py'`).
  if [[ -r "/proc/${pid}/comm" ]]; then
    read -r comm < "/proc/${pid}/comm" || comm=""
    case "$comm" in
      bash | sh | dash | zsh | fish) continue ;;
    esac
  fi
  if tr '\0' ' ' < "/proc/${pid}/cmdline" | grep -qF "$REPO_ROOT"; then
    kill "-${sig}" "$pid" 2>/dev/null && killed+=("$pid") || true
  fi
done
if ((${#killed[@]})); then
  echo "sent ${sig} to PIDs: ${killed[*]}"
else
  echo "no nfc-os main.py process found under ${REPO_ROOT}" >&2
fi
