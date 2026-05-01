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
- Venv: `.venv` with deps installed (`pip install -r requirements.txt`); **`pyscard`** must be installed for USB PC/SC readers.
- **`PYTHONPATH`** must include `src` (see launch command below).
- **SSH → Pi, GUI on HDMI/local X**: the session often has no `DISPLAY`; target the logged-in X server with `DISPLAY=:0` and `XAUTHORITY=$HOME/.Xauthority` (typical for Raspberry Pi OS + `startx`).
- **USB NFC reader (PC/SC, e.g. ACR1252U)** when testing real taps:
  - Reader plugged in; **`pcscd`** running (`systemctl is-active pcscd`).
  - User can use PC/SC **without sudo** (`pcsc_scan` works as the same user that runs `main.py`). If you get `SCardEstablishContext: Access denied`, install the polkit rule once: `sudo bash deploy/pi/install-polkit-pcsc.sh` from repo root (see the **USB NFC reader** section in the repo `README.md`).
  - Launch with **`NFC_OS_USE_PCSC=1`** (included in the default command below).
  - Optional: **`NFC_OS_PCSC_READER=ACR1252`** (substring) if several PC/SC devices exist.

To exercise **only** the stdin / **Test tags** mock (no USB reader), run the same command **without** `NFC_OS_USE_PCSC` (or set `NFC_OS_USE_PCSC=0`).

## Steps (agent must follow in order)

1. **Shell** (adjust path if workspace root differs):

   ```bash
   cd /opt/nfc-os
   . .venv/bin/activate
   export PYTHONPATH=/opt/nfc-os/src
   export NFC_OS_USE_PCSC=1
   export QTWEBENGINE_CHROMIUM_FLAGS="${QTWEBENGINE_CHROMIUM_FLAGS:---disable-gpu --disable-gpu-compositing --no-sandbox --disable-dev-shm-usage}"
   DISPLAY=:0 XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}" \
     timeout 120 python -u main.py 2>&1 | tee /tmp/nfc-os-gui-last.log
   ```

   - **`NFC_OS_USE_PCSC=1`** turns on the pyscard observer so taps on the USB reader enqueue the same `tag_in` / `tag_out` events as the dev field.
   - Use **`timeout 120`** (or similar) so the agent shell does not block forever; the user can watch the GUI during that window. Increase only if the user asks for a longer run.
   - If the user wants the app to stay up until they close it manually, run the same command **without** `timeout` in the **background** (`block_until_ms: 0`), then still ask for feedback.

2. **If the command fails**, read `/tmp/nfc-os-gui-last.log` (and stderr from the tool result), fix what you can, retry once if appropriate.

3. **After the run** (or after starting background launch), **do not** mark the task done based on exit code alone. **Ask the user directly**, for example:
   - “Did the NFC OS window appear fullscreen? Was idle text visible? Any errors on screen?”
   - **NFC:** “Did a tap on a configured tag launch the cartridge? Unknown tags should toast with the UID (add it to `config/tags.json`). Did removing the tag eject when `presence_mode` is true?”
   - **URL cartridges on the Pi:** they spawn a real browser (chromium / firefox) on `DISPLAY=:0`; tag removal kills it. If no browser is installed the running screen prints a clear hint (`sudo apt install chromium`).
   - They can still use the bottom **Test tags** field (`+UID`, `-`, `quit`) for mock events; remind them mixing mock `+UID` with a live tag on the reader can confuse presence state.

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
- Do not skip **`NFC_OS_USE_PCSC=1`** when the user wants to **test the USB reader**—without it, only stdin / dev-line mock events reach the supervisor.
- Do not skip the **feedback question** after launching.
