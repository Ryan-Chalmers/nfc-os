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

If `pip install pyscard` fails to build the native extension, install PC/SC headers and SWIG, then retry:

```bash
sudo apt install -y swig libpcsclite-dev
```

### USB NFC reader (PC/SC, e.g. ACS ACR1252U)

The ACR1252U and similar readers use the system **PC/SC** stack (`pcscd`), not a custom kernel driver.

1. Install the daemon, client library, CCID driver, and the `pcsc_scan` diagnostic tool:

   ```bash
   sudo apt update
   sudo apt install -y pcscd libpcsclite1 libccid pcsc-tools
   sudo systemctl enable --now pcscd
   ```

2. Plug in the reader and run `pcsc_scan`. You should see the reader by name; tapping a tag should show activity. If the reader never appears, confirm USB power and try the vendor **acsccid** driver from ACS for your OS version.

3. **Polkit (no sudo for `pcsc_scan` / NFC OS):** On many images, `pcsc_scan` without sudo fails with `SCardEstablishContext: Access denied` even when `/run/pcscd/pcscd.comm` is world-writable. A rule under `/etc/polkit-1/rules.d/` fixes that **permanently** (it survives reboots). To reproduce on another machine from this repo:

   ```bash
   cd /opt/nfc-os   # or your checkout path
   sudo bash deploy/pi/install-polkit-pcsc.sh          # uses the user who ran sudo
   sudo bash deploy/pi/install-polkit-pcsc.sh kiosk   # or pass the kiosk username explicitly
   ```

4. Run NFC OS with PC/SC enabled (see `NFC_OS_USE_PCSC` below).

## Run (default: Qt fullscreen)

From the repository root:

```bash
export PYTHONPATH=src
python main.py
```

With a USB PC/SC reader (after installing `pcscd` and `pyscard`):

```bash
export PYTHONPATH=src
NFC_OS_USE_PCSC=1 python main.py
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
| `url` | On ARM (Raspberry Pi) launches a **system browser** on `DISPLAY=:0` (Chromium / Firefox) and kills it on eject; on other platforms loads in embedded **Qt WebEngine**. See `NFC_OS_URL_*` / `NFC_OS_BROWSER*` envs. |
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
| `NFC_OS_USE_PCSC` | If `1`, `true`, `yes`, or `on`, run a PC/SC reader monitor so USB readers feed the same event queue as the stdin/dev mock. By default the monitor runs in a **separate child process** (`python -m nfc_os.readers.pcsc_worker_main`) so `smartcard.scard` is never loaded inside the Qt process; this avoids native crashes alongside WebEngine. Set `NFC_OS_PCSC_INPROCESS=1` to use the legacy in-thread `CardMonitor` (debug only) |
| `NFC_OS_PCSC_READER` | Optional substring (case-insensitive) to match one reader when multiple PC/SC devices exist (e.g. `ACR1252`) |
| `NFC_OS_PCSC_INPROCESS` | If truthy, register the in-process pyscard `CardMonitor` instead of the subprocess monitor (debug only) |
| `NFC_OS_URL_EXTERNAL` | Force URL cartridges to launch a system browser even on non-ARM hosts |
| `NFC_OS_URL_EMBEDDED` | Force URL cartridges to use embedded Qt WebEngine even on ARM (crash-prone on Raspberry Pi) |
| `NFC_OS_BROWSER` | Path or name of the browser binary to use for URL cartridges (e.g. `/usr/bin/chromium`, `firefox-esr`). Without this, NFC OS searches `chromium-browser`, `chromium`, `firefox-esr`, `firefox`, `epiphany-browser`, `midori`, `falkon` and ignores `$BROWSER` (Cursor SSH sets it to a host helper that opens URLs on the workstation) |
| `NFC_OS_BROWSER_KIOSK` | If truthy, pass `--kiosk` to Chromium-style browsers |
| `NFC_OS_DROP_BROWSER_ENV` | If truthy, also strip `$BROWSER` even when it doesn't look like a Cursor SSH helper |
| `NFC_OS_WEBENGINE_VERBOSE` | If truthy, append Chromium `--enable-logging=stderr --v=1` to `QTWEBENGINE_CHROMIUM_FLAGS` (only relevant when embedded WebEngine is in use) |
| `NFC_OS_CURSOR_HIDE_MS` | Milliseconds of pointer/keyboard inactivity before hiding the cursor (default `4000`, clamped 500–60000) |

When `NFC_OS_USE_PCSC` is enabled, the Qt shell still accepts stdin and **Test tags** dev lines; avoid mixing fake `+UID` events with a live tag on the reader if you care about consistent presence state.

### URL cartridges on Raspberry Pi

Embedded Qt WebEngine + Chromium tends to SIGSEGV on Raspberry Pi when the renderer first tries to paint a complex page. NFC OS detects ARM at startup and launches **a real system browser** on the local display instead. Install one if you don't already have it:

```bash
sudo apt install -y chromium       # Debian trixie / current Raspberry Pi OS
# or:
sudo apt install -y chromium-browser   # older releases
# or:
sudo apt install -y firefox-esr
```

Tag eject (`tag_out`, double-scan, home UID) terminates the browser child so the home screen returns immediately.

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
- `src/nfc_os/readers/` — HAL (`MockReader` for CLI), stdin event source for Qt mock, optional `pcsc_subprocess` (default; runs `pcsc_worker_main` as a child) and `pcsc_events` (legacy in-thread `CardMonitor`) for USB PC/SC readers

## Module entrypoint

```bash
PYTHONPATH=src python -m nfc_os.ui
```
