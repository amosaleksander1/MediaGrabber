"""MediaGrabber — portable, menu-driven media downloader.

Public entry point:

    from mediagrabber import main
    main()
"""

APP_NAME = "MediaGrabber"
APP_VERSION = "3.2.0"

__all__ = ["APP_NAME", "APP_VERSION", "main"]


def main():  # lazy import keeps `import mediagrabber` cheap
    from .app import main as _main
    return _main()
