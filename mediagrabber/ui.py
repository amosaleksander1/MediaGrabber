"""Terminal output: colours, logging, banner, pickers, menu."""

import datetime
import sys
from pathlib import Path

from . import APP_VERSION
from .platform_support import OS_LABEL, PLATFORM_TAG


class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    # Legacy cmd.exe ignores italic; the dim it is paired with still reads.
    ITALIC = "\033[3m"
    REVERSE = "\033[7m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"


def _renderable(text):
    """Can this console encode these characters at all?"""
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        text.encode(enc)
        return True
    except (UnicodeEncodeError, LookupError):
        return False


#: Selected / unselected marks for the settings screen. These are radio
#: buttons, not checkboxes — mode, format and resolution are each one-of-N —
#: so a filled box means "this one", never "also this one".
MARK_ON, MARK_OFF = ("▣", "▢") if _renderable("▣▢") else ("(*)", "( )")


_LOG_FILE = None

#: While true, routine output goes to the session log only. Startup has a lot
#: to say and almost none of it is news — checking five tools that are all fine
#: pushed the title off the screen. The rule is deliberately "everything except
#: a warning or an error", rather than a list of lines to hide, so a log line
#: added later cannot quietly put the spam back.
_QUIET = False

#: Levels that are worth interrupting a quiet stretch for.
_ALWAYS_SHOW = ("WARN", "ERROR")

_COLOR_MAP = {
    "INFO": C.WHITE,
    "OK": C.GREEN,
    "WARN": C.YELLOW,
    "ERROR": C.RED,
    "DOWNLOAD": C.CYAN,
    "UPDATE": C.MAGENTA,
    "HEADER": C.BOLD + C.BLUE,
}


def init_logging(logs_dir):
    global _LOG_FILE
    Path(logs_dir).mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    _LOG_FILE = Path(logs_dir) / f"session_{stamp}.log"
    return _LOG_FILE


def log_file():
    return _LOG_FILE


class quiet_output:
    """Send routine log lines to the file only, for the duration of a block.

    Warnings and errors still print: staying quiet is for the case where there
    is nothing to say, not for hiding a problem.
    """

    def __enter__(self):
        global _QUIET
        self._was = _QUIET
        _QUIET = True
        return self

    def __exit__(self, *exc):
        global _QUIET
        _QUIET = self._was
        return False


def log(msg, level="INFO", color=None):
    """Print to the terminal with colour and append to the session log."""
    stamp = datetime.datetime.now().strftime("%H:%M:%S")
    c = color or _COLOR_MAP.get(level, C.WHITE)
    if not _QUIET or level in _ALWAYS_SHOW:
        print(f"{c}[{stamp}] [{level}]{C.RESET} {msg}")
    if _LOG_FILE:
        try:
            with open(_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"[{stamp}] [{level}] {msg}\n")
        except Exception:
            pass


def banner_lines():
    """The title box as a list of lines, so a redrawn screen can reuse it."""
    name = f"MediaGrabber v{APP_VERSION}"
    byline = "by Amos Aleksander"
    sub = f"{OS_LABEL} · {PLATFORM_TAG}"

    # Centre on the *visible* text: the colour escapes carry no width, so
    # measuring the finished string would push the box crooked.
    plain = f"{name}   {byline}"
    left = " " * ((54 - len(plain)) // 2)
    right = " " * (54 - len(plain) - len(left))
    titled = (f"{left}{C.WHITE}{name}{C.RESET}{C.BOLD}{C.CYAN}   "
              f"{C.DIM}{C.ITALIC}{byline}{C.RESET}{C.BOLD}{C.CYAN}{right}")

    return [
        f"{C.BOLD}{C.CYAN}╔══════════════════════════════════════════════════════╗",
        f"║{titled}║",
        f"║{C.RESET}{C.DIM}{sub.center(54)}{C.CYAN}{C.BOLD}║",
        f"║{'Portable Media Downloader + Auto-Update'.center(54)}║",
        f"╚══════════════════════════════════════════════════════╝{C.RESET}",
    ]


def banner():
    print()
    for line in banner_lines():
        print(line)
    print()


def rule(width=55):
    print(f"{C.DIM}{'─' * width}{C.RESET}")


def pick_from_list(title, items, current=None):
    """Numbered picker. ``items`` = [(value, label), ...]. Returns value/None."""
    print(f"\n  {C.BOLD}{C.CYAN}{title}{C.RESET}")
    print(f"  {C.DIM}{'─' * 45}{C.RESET}")
    for i, (val, label) in enumerate(items, 1):
        marker = f" {C.GREEN}<- current{C.RESET}" if val == current else ""
        print(f"  {C.GREEN}[{i:>2}]{C.RESET} {label}{marker}")
    print(f"  {C.DIM}[0]  Cancel{C.RESET}\n")
    try:
        choice = input(f"  {C.CYAN}#{C.RESET} ").strip()
        if not choice or choice == "0":
            return None
        idx = int(choice) - 1
        if 0 <= idx < len(items):
            return items[idx][0]
        log("Invalid selection.", "WARN")
    except (ValueError, EOFError):
        pass
    return None
