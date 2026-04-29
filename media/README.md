# Local media (`media/`)

Drop video and audio files here. **Nothing in this folder is meant to be committed** (except this file).

## Playback (mpv)

Use the helper script from a cartridge `command` payload (see `config/tags.json` tag `LOCAL0001`).

- **Relative file** (under `media/`): pass the filename only, e.g. `demo.mp4`
- **Absolute path**: pass a path starting with `/`, e.g. `/mnt/usb/film.mkv`

Install mpv on the Pi once:

```bash
sudo apt update && sudo apt install -y mpv
```

## Example

1. Copy a file to `media/demo.mp4`
2. In the app dev bar: `+LOCAL0001`
3. Eject: `-` (stops mpv like any other command cartridge)

If your repo is not at `/opt/nfc-os`, edit the `command` payload in `tags.json` to match your checkout path.
