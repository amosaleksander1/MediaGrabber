#!/usr/bin/env python3
"""MediaGrabber launcher.

The implementation lives in the ``mediagrabber`` package next to this file;
this shim exists so the historic ``python mediagrabber.py`` entry point keeps
working, and so PyInstaller has a single obvious script to freeze.
"""

import sys
from pathlib import Path

# When frozen, the package is bundled; when running from source, make sure the
# repo root is importable no matter which directory the user launched from.
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from mediagrabber import main  # noqa: E402

if __name__ == "__main__":
    main()
