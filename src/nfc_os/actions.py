from __future__ import annotations

import subprocess
from pathlib import Path


def run_command(payload: str) -> str:
    """Execute a Linux command string and return stdout."""
    completed = subprocess.run(
        payload,
        shell=True,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def run_script(payload: str) -> str:
    """Execute a local script path and return stdout."""
    script_path = Path(payload).expanduser().resolve()
    completed = subprocess.run(
        [str(script_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def media_control(payload: str) -> str:
    """Stub for future media stack integration."""
    return f"media_control stub received payload={payload}"
