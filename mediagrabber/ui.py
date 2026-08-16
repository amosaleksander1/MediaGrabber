"""Terminal output: colours, logging, banner, pickers, menu."""

import datetime
from pathlib import Path

from . import APP_VERSION
from .platform_support import OS_LABEL, PLATFORM_TAG


class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"


_LOG_FILE = None

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


def log(msg, level="INFO", color=None):
    """Print to the terminal with colour and append to the session log."""
    stamp = datetime.datetime.now().strftime("%H:%M:%S")
    c = color or _COLOR_MAP.get(level, C.WHITE)
    print(f"{c}[{stamp}] [{level}]{C.RESET} {msg}")
    if _LOG_FILE:
        try:
            with open(_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"[{stamp}] [{level}] {msg}\n")
        except Exception:
            pass


def banner():
    title = f"MediaGrabber v{APP_VERSION}"
    sub = f"{OS_LABEL} · {PLATFORM_TAG}"
    print(f"""
{C.BOLD}{C.CYAN}╔══════════════════════════════════════════════════════╗
║{C.WHITE}{title.center(54)}{C.CYAN}║
║{C.RESET}{C.DIM}{sub.center(54)}{C.CYAN}{C.BOLD}║
║{"Portable Media Downloader + Auto-Update".center(54)}║
╚══════════════════════════════════════════════════════╝{C.RESET}
""")


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
