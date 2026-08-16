#!/bin/bash
# MediaGrabber — Linux / macOS launcher.
cd "$(dirname "$0")" || exit 1

if [ -x "./MediaGrabber" ]; then
    exec "./MediaGrabber"
fi

for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1; then exec "$c" mediagrabber.py "$@"; fi
done

echo "Python 3 was not found on PATH."
exit 1
