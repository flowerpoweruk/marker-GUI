#!/bin/bash
# Double-click this in Finder to start Marker Studio.
cd "$(dirname "$0")" || exit 1

# Find a usable Python 3 (3.10+).
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3 python; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done

if [ -z "$PY" ]; then
  echo ""
  echo "  Python 3 isn't installed."
  echo "  Get it from https://www.python.org/downloads/ then try again."
  echo ""
  read -r -p "  Press Enter to close…" _
  exit 1
fi

"$PY" bootstrap.py
