#!/bin/bash
# MediaGrabber — macOS double-clickable launcher.
# Finder runs .command files in Terminal, which is what the menu UI needs.

cd "$(dirname "$0")" || exit 1

# Finder-launched processes inherit a minimal PATH; add the usual Homebrew and
# Command Line Tools locations so a system python3 is discoverable.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

# Prefer the bundled binary if this is a release download.
if [ -x "./MediaGrabber" ]; then
    exec "./MediaGrabber"
fi

PY=""
for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done

if [ -z "$PY" ]; then
    echo "Python 3 was not found."
    echo "Install it with:  xcode-select --install"
    echo "…or from https://www.python.org/downloads/macos/"
    echo
    read -r -p "Press Enter to close."
    exit 1
fi

"$PY" mediagrabber.py
status=$?

if [ $status -ne 0 ]; then
    echo
    echo "MediaGrabber exited with status $status."
    read -r -p "Press Enter to close."
fi
