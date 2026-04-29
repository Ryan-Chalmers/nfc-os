---
name: kill-nfc-os
description: >-
  Stops nfc-os processes (python main.py) launched from this repository so GUI
  or CLI runs can be torn down before a new /run-nfc-os. Use when the user says
  /kill-nfc-os, asks to stop nfc-os, kill the kiosk GUI, or clear stuck python
  main.py.
---

# Stop nfc-os (`main.py`)

## When to use

Apply when the user wants to **terminate running nfc-os** (fullscreen GUI, background agent launch, or stray `python main.py`) **without** killing unrelated Python apps.

## Command (agent or user)

From any cwd (script resolves repo root):

```bash
bash /opt/nfc-os/scripts/kill-nfc-os.sh
```

Or from repo root:

```bash
bash scripts/kill-nfc-os.sh
```

Optional first argument: signal name (default `TERM`):

```bash
bash scripts/kill-nfc-os.sh KILL
```

## Behavior

- Finds PIDs whose command line matches `main.py` **and** contains this repo’s absolute path (`/proc/<pid>/cmdline`), then sends the signal.
- Prints which PIDs were signaled, or a message if none matched (exit still 0 so scripts stay idempotent).

## After killing

If the user was doing a GUI check, they can **`/run-nfc-os`** again once the old process is gone.
