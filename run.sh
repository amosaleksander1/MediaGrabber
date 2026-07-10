#!/usr/bin/env bash
# MediaGrabber launcher for Linux
cd "$(dirname "$0")"
exec python3 mediagrabber.py "$@"
