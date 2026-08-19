#!/usr/bin/env python3
"""Entry point for the native-messaging bridge.

Separate from mediagrabber.py on purpose. The browser launches this with stdio
wired to the extension and passes arguments that differ per engine — Chrome
sends the extension origin (plus --parent-window on Windows), Firefox sends the
manifest path and the extension ID. Deciding "am I a bridge or the menu app?"
from those two shapes inside one binary is exactly the kind of guess that breaks
quietly, so the bridge gets its own executable.

Never print to stdout from here: stdout is the protocol.
"""

import sys

from mediagrabber.bridge import main

if __name__ == "__main__":
    sys.exit(main(sys.argv))
