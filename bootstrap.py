#!/usr/bin/env python3
"""
Marker Studio bootstrapper.

Runs with the system Python (3.10+). On first launch it builds an isolated
environment and installs everything Marker Studio needs; every launch after
that skips straight to opening the app. The user never types a command.
"""

import os
import sys
import time
import venv
import platform
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
STAMP = VENV / ".installed-v1"

REQUIREMENTS = [
    "marker-pdf[full]>=1.10.0",
    "fastapi>=0.115",
    "uvicorn>=0.30",
    "python-multipart>=0.0.9",
]


def banner(msg):
    line = "─" * (len(msg) + 2)
    print(f"\n┌{line}┐\n│ {msg} │\n└{line}┘\n")


def venv_python() -> Path:
    if platform.system() == "Windows":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def check_python():
    if sys.version_info < (3, 10):
        banner("Marker Studio needs Python 3.10 or newer")
        print("  You're on Python", platform.python_version())
        print("  Install a newer Python from https://www.python.org/downloads/")
        print("  then double-click Marker Studio again.\n")
        input("  Press Enter to close…")
        sys.exit(1)


def ensure_venv():
    if not venv_python().exists():
        banner("Setting up Marker Studio (first launch only)")
        print("  Creating an isolated environment…")
        venv.EnvBuilder(with_pip=True).create(VENV)


def pip(*args):
    return subprocess.run([str(venv_python()), "-m", "pip", *args], check=True)


def ensure_deps():
    if STAMP.exists():
        return
    banner("Installing Marker — this downloads a few GB and runs once")
    print("  Grab a coffee. Subsequent launches are instant.\n")
    pip("install", "--upgrade", "pip", "setuptools", "wheel")
    pip("install", *REQUIREMENTS)
    STAMP.write_text(f"installed {time.ctime()}\n")
    print("\n  Setup complete.\n")


def launch():
    banner("Launching Marker Studio")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    # Hand off to the venv's Python running the server.
    proc = subprocess.run(
        [str(venv_python()), "-m", "server.app"],
        cwd=str(ROOT),
        env=env,
    )
    return proc.returncode


def main():
    print("\n  Marker Studio")
    print("  ─────────────")
    check_python()
    try:
        ensure_venv()
        ensure_deps()
    except subprocess.CalledProcessError as e:
        banner("Setup hit a problem")
        print("  The installer failed. Most often this is a network issue —")
        print("  check your connection and double-click Marker Studio again.")
        print(f"\n  Details: {e}\n")
        input("  Press Enter to close…")
        sys.exit(1)

    code = launch()
    if code != 0:
        input("\n  The app closed unexpectedly. Press Enter to exit…")


if __name__ == "__main__":
    main()
