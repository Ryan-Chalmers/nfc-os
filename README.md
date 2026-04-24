# NFC OS (Raspberry Pi cartridge shell)

NFC-driven “cartridge” launcher with a **fullscreen Qt shell** (PySide6), a **supervisor** that tracks subprocesses and optional **Qt WebEngine** URL cartridges, and a **stdin mock** for presence events over SSH.

## Requirements

- **64-bit** Raspberry Pi OS (Bookworm or newer) for PySide6 + WebEngine parity.
- **Python 3.11 or 3.12** for the venv: PySide6 publishes wheels for these versions. **Python 3.14** often has **no PySide6 wheel yet**, so `pip install` can fail. This repo includes [`.python-version`](.python-version) set to `3.12` for pyenv and similar tools.

## Install

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run (default: Qt fullscreen)

From the repository root:

```bash
export PYTHONPATH=src
python main.py
```

Stdin mock NFC (works over SSH):

| Input | Meaning |
| --- | --- |
| `+AA11BB22` | Tag inserted (UID `AA11BB22`) |
| `-` | Tag removed (presence out) |
| `quit` | Stop stdin reader and shut down |

### CLI legacy loop (no Qt)

```bash
PYTHONPATH=src python main.py --cli
```

## Configuration (`config/tags.json`)

Top-level **`meta`**:

| Field | Description |
| --- | --- |
| `home_uids` | String or list of UIDs that **force eject** to idle while a cartridge is running |
| `double_scan_eject` | If true, scanning the **same** UID again while running ejects |
| `presence_mode` | If true, a `-` **tag_out** event ejects while running |

Each tag entry uses **`kind`** + **`payload`**:

| `kind` | Behavior |
| --- | --- |
| `url` | Loads URL in embedded **Qt WebEngine** (fallback label if WebEngine missing) |
| `command` | `shell=True` subprocess while running; eject kills it |
| `script` | Executes script path; eject kills it |
| `media_control` | Runs the synchronous stub; eject with `-` / home / double-scan |

Legacy entries using `action` + `payload` (`run_command`, `run_script`, `media_control`) still load via `nfc_os.cartridge.load_cartridge_config`.

## Environment variables

| Variable | Purpose |
| --- | --- |
| `NFC_OS_UI=cli` | Same as `python main.py --cli` |
| `NFC_OS_CONFIG` | Absolute path to an alternate `tags.json` |
| `NFC_OS_LOG_FILE` | Append structured logs to a file |
| `NFC_OS_HOME` | Root install path for `deploy/pi/run-nfc-os.sh` |

## Raspberry Pi graphical session (minimal X11 + Openbox)

1. Install a minimal stack (example package names; adjust for your image):

   ```bash
   sudo apt update
   sudo apt install -y xserver-xorg xinit openbox x11-xserver-utils
   ```

2. Copy this repo to `/opt/nfc-os` (or set `NFC_OS_HOME`).

3. Create a venv on the Pi and `pip install -r requirements.txt`.

4. Configure **Openbox autostart** to launch NFC OS after the window manager starts, e.g. `~/.config/openbox/autostart`:

   ```bash
   /opt/nfc-os/deploy/pi/run-nfc-os.sh &
   ```

5. Start X11 on boot with **auto-login to console** and `startx` (classic pattern), or install **lightdm** and add a custom session using `deploy/pi/xsession-openbox.desktop.example` as a template.

6. Ensure `DISPLAY=:0` and `XAUTHORITY` match the logged-in user (see `deploy/nfc-os.service`).

### systemd user service (graphical target)

`deploy/nfc-os.service` assumes user **`pi`**, install path **`/opt/nfc-os`**, and an already-running X server on `:0`. Edit `User=`, paths, and `XAUTHORITY` before enabling:

```bash
sudo cp deploy/nfc-os.service /etc/systemd/system/nfc-os.service
sudo systemctl daemon-reload
sudo systemctl enable nfc-os
sudo systemctl start nfc-os
sudo systemctl status nfc-os
```

## Project layout (high level)

- `main.py` — entry (`Qt` default, `--cli` for legacy controller)
- `src/nfc_os/ui/app.py` — fullscreen shell + UI queue pump
- `src/nfc_os/supervisor.py` — cartridge state machine, child watcher, stdin events
- `src/nfc_os/cartridge.py` — config parsing + launcher helpers
- `src/nfc_os/readers/` — HAL (`MockReader` for CLI) and stdin event source for Qt mock

## Module entrypoint

```bash
PYTHONPATH=src python -m nfc_os.ui
```
