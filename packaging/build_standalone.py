#!/usr/bin/env python3
"""
OPTIONAL: build a true standalone Marker Studio app (.app / .exe) so people
who don't have Python can double-click and run.

This is the advanced path. It produces a large bundle (PyTorch alone is ~2.5GB)
and must be built separately on each OS you want to ship to — a macOS build
only runs on macOS, a Windows build only on Windows.

For most people the normal launcher (Marker Studio.command / .bat) is simpler
and recommended. Use this only when distributing to non-technical users.

Usage:
    # from a machine that already ran the app once (so .venv exists):
    .venv/bin/python packaging/build_standalone.py        # macOS / Linux
    .venv\\Scripts\\python packaging\\build_standalone.py   # Windows
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    # PyInstaller is only needed for this optional build.
    subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)

    sep = ";" if sys.platform.startswith("win") else ":"
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--name", "Marker Studio",
        "--windowed",
        "--add-data", f"{ROOT / 'web'}{sep}web",
        # marker + surya load many resources dynamically; collect them whole.
        "--collect-all", "marker",
        "--collect-all", "surya",
        "--collect-all", "pdftext",
        "--collect-all", "torch",
        "--collect-all", "transformers",
        "--collect-data", "filetype",
        str(ROOT / "server" / "app.py"),
    ]
    print("Running PyInstaller — this takes a while and produces a multi-GB bundle.\n")
    subprocess.run(args, cwd=str(ROOT), check=True)
    print("\nDone. Find your app under dist/Marker Studio/.")
    print("Note: the AI models still download on first conversion.")


if __name__ == "__main__":
    main()
