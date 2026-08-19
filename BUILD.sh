#!/bin/bash
# Build a standalone MediaGrabber binary for macOS or Linux.
#
#   ./BUILD.sh
#
# Output lands next to this script. On macOS the result is a native binary for
# whichever architecture you build on — build on Apple Silicon for arm64, on an
# Intel Mac (or under Rosetta) for x86_64.

set -euo pipefail
cd "$(dirname "$0")"

echo "============================================"
echo "  Building MediaGrabber"
echo "============================================"

PY=""
for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
    echo "ERROR: Python 3 not found on PATH."
    exit 1
fi

echo "Python:       $($PY --version)"
echo "Architecture: $(uname -m)"
echo "Platform:     $(uname -s)"
echo

echo "Installing PyInstaller..."
"$PY" -m pip install --upgrade --quiet pyinstaller

echo "Compiling..."
"$PY" -m PyInstaller \
    --onefile \
    --console \
    --name MediaGrabber \
    --clean \
    --noconfirm \
    --distpath "$PWD" \
    --hidden-import mediagrabber \
    --collect-submodules mediagrabber \
    mediagrabber.py

# The browser launches this one directly, so it is a separate program: Chrome
# and Firefox pass different arguments, and its stdout is the wire protocol.
echo "Compiling the browser bridge..."
"$PY" -m PyInstaller \
    --onefile \
    --console \
    --name mediagrabber-bridge \
    --clean \
    --noconfirm \
    --distpath "$PWD" \
    --hidden-import mediagrabber \
    --collect-submodules mediagrabber \
    bridge_main.py

rm -rf build __pycache__ MediaGrabber.spec mediagrabber-bridge.spec

# A binary produced locally is not quarantined, but one copied from a download
# is — clear the attribute so Gatekeeper does not block the first launch.
if [ "$(uname -s)" = "Darwin" ]; then
    xattr -dr com.apple.quarantine ./MediaGrabber ./mediagrabber-bridge 2>/dev/null || true
fi

chmod +x ./MediaGrabber ./mediagrabber-bridge

echo
echo "============================================"
echo "  BUILD COMPLETE"
echo "  ./MediaGrabber is ready."
echo "============================================"
