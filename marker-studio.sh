#!/bin/bash
# Run this to start Marker Studio (chmod +x once, then double-click or ./marker-studio.sh).
cd "$(dirname "$0")" || exit 1

PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3 python; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done

if [ -z "$PY" ]; then
  echo ""
  echo "  Python 3.10+ isn't installed."
  echo "  Install it with your package manager (e.g. sudo apt install python3 python3-venv)."
  echo ""
  read -r -p "  Press Enter to close…" _
  exit 1
fi

"$PY" bootstrap.py
