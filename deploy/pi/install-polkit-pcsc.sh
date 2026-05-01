#!/usr/bin/env bash
# Install polkit rules so a normal user can use pcscd (e.g. pcsc_scan, NFC_OS_USE_PCSC)
# without sudo. Survives reboots. Run as root, typically from the account that runs NFC OS.
#
#   sudo bash deploy/pi/install-polkit-pcsc.sh
#   sudo bash deploy/pi/install-polkit-pcsc.sh kiosk
set -euo pipefail

target_user="${1:-${SUDO_USER:-}}"
if [[ -z "${target_user}" ]]; then
  echo "Usage: sudo $0 [<username>]" >&2
  echo "  If <username> is omitted, uses SUDO_USER (the user who invoked sudo)." >&2
  exit 1
fi
if [[ "${target_user}" == "root" ]]; then
  echo "Refusing to install a rule for root only; pass the desktop/kiosk username." >&2
  exit 1
fi

rules_path="/etc/polkit-1/rules.d/50-nfc-os-pcsc.rules"

tee "${rules_path}" >/dev/null <<EOF
polkit.addRule(function(action, subject) {
    if (subject.user != "${target_user}") {
        return polkit.Result.NOT_HANDLED;
    }
    if (action.id == "org.debian.pcsc-lite.access_pcsc" ||
        action.id == "org.debian.pcsc-lite.access_card") {
        return polkit.Result.YES;
    }
    return polkit.Result.NOT_HANDLED;
});
EOF
chmod 644 "${rules_path}"

if systemctl is-system-running >/dev/null 2>&1; then
  systemctl try-restart polkit.service 2>/dev/null \
    || systemctl try-restart polkitd.service 2>/dev/null \
    || true
  systemctl try-restart pcscd.service 2>/dev/null || true
fi

echo "Installed ${rules_path} for user ${target_user} (persistent across reboots)."
