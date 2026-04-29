#!/usr/bin/env bash
set -euo pipefail
cd /opt/nfc-os
source .venv/bin/activate
export PYTHONPATH=/opt/nfc-os/src
exec python main.py
