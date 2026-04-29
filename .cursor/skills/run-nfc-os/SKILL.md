---
name: run-nfc-os
description: >-
  Launches nfc-os Qt GUI on the Pi’s local display from SSH (DISPLAY=:0) so the
  user can see it, then stops and asks what they observed. Use when the user
  says /run-nfc-os, asks to run the GUI for visual check, kiosk smoke test, or
  “run it on the display and wait for my feedback”.
---

# Run nfc-os GUI and wait for feedback

## When to use

Apply this skill when the user wants the agent to **start the real app on the hardware display** and **not assume success**—the agent must **run the command**, then **ask the user what they see** (or whether it worked) before continuing.

## Preconditions

- Repo root: `/opt/nfc-os` (or current workspace root if different).
- Venv: `.venv` with deps installed; `PYTHONPATH` must include `src`.
- **SSH → Pi, GUI on HDMI/local X**: the session often has no `DISPLAY`; target the logged-in X server with `DISPLAY=:0` and `XAUTHORITY=$HOME/.Xauthority` (typical for Raspberry Pi OS + `startx`).

## Steps (agent must follow in order)

1. **Shell** (adjust path if workspace root differs):

   ```bash
   cd /opt/nfc-os
   . .venv/bin/activate
   export PYTHONPATH=/opt/nfc-os/src
   DISPLAY=:0 XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}" \
     timeout 120 python -u main.py 2>&1 | tee /tmp/nfc-os-gui-last.log
   ```

   - Use **`timeout 120`** (or similar) so the agent shell does not block forever; the user can watch the GUI during that window. Increase only if the user asks for a longer run.
   - If the user wants the app to stay up until they close it manually, run the same command **without** `timeout` in the **background** (`block_until_ms: 0`), then still ask for feedback.

2. **If the command fails**, read `/tmp/nfc-os-gui-last.log` (and stderr from the tool result), fix what you can, retry once if appropriate.

3. **After the run** (or after starting background launch), **do not** mark the task done based on exit code alone. **Ask the user directly**, for example:
   - “Did the NFC OS window appear fullscreen? Was idle text visible? Any errors on screen?”
   - If they need the dev bar: remind them they can use `+UID`, `-`, `quit` in the bottom field.

4. **Wait for the user’s reply** before declaring success or doing follow-up changes that depend on what they saw.

## Optional: windowed debug (only if user asks)

Fullscreen is default. For a smaller window (debug only), the codebase may support env toggles; prefer the user’s explicit request before changing launch flags.

## Stop GUI / stuck runs (before re-run)

```bash
bash /opt/nfc-os/scripts/kill-nfc-os.sh
```

See the **kill-nfc-os** skill (`.cursor/skills/kill-nfc-os/`) for full semantics.

## Anti-patterns

- Do not run `python main.py` over SSH **without** `DISPLAY=:0` (and `XAUTHORITY` when needed) and assume the user saw anything on the Pi monitor.
- Do not skip the **feedback question** after launching.
