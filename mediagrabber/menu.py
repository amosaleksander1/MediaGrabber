"""What the main screen looks like, and what a click at (x, y) means.

Kept apart from the terminal on purpose. Building the screen is a pure
function — config in, lines and click-boxes out — so the layout and the
hit-testing can be tested without a console, which is the half of the
interactive menu that is otherwise impossible to check automatically.

The settings are on the main screen rather than behind a menu entry: mode,
format and resolution are the things you change most often, and making them a
destination meant three keystrokes and a screen of scrollback to flip one
value. Here they are rows you land on with the arrow keys or click directly.
"""

import re
import shutil
from collections import namedtuple

from .config import (AUDIO_FORMATS, RESOLUTION_OPTIONS, VIDEO_FORMATS)
from .platform_support import OS_LABEL, PLATFORM_TAG
from .ui import C, MARK_OFF, MARK_ON, banner_lines

_ANSI_RE = re.compile(r"\033\[[0-9;]*[A-Za-z]")

#: A clickable region on the rendered screen. ``target`` is what activating it
#: does: ("set", config key, value) or ("action", action name).
Hit = namedtuple("Hit", "y x0 x1 target row col")

#: ``rows`` is the navigable list, in Up/Down order — ("settings", key, values)
#: or ("action", name, None). ``hits`` is every clickable box.
Layout = namedtuple("Layout", "lines hits rows")

#: number key, action name, label, hint. The numbers double as shortcuts in the
#: interactive menu and as the whole interface in the piped fallback, so the two
#: never drift apart.
ACTIONS = [
    ("1", "single", "Download Single URL", ""),
    ("2", "batch", "Download Batch", "from urls.txt"),
    ("3", "edit", "Edit Batch URLs", "opens urls.txt"),
    ("4", "login", "Login & Browser", ""),
    ("5", "update", "Tools Update", ""),
    ("6", "output", "Open Output Folder", ""),
    ("7", "folder", "Change Output Folder", ""),
    ("0", "exit", "Exit", ""),
]

_LABEL_W = 14
_INDENT = "  "

#: Narrower than any console anyone still uses, so a row that has to wrap wraps
#: the same way everywhere. Ten audio formats do not fit on one line.
_MIN_WIDTH = 60


def screen_width():
    """Usable columns, minus one so a full line never wraps on its own.

    A wrap the layout did not decide on is what breaks clicking: the terminal
    would push every following row down a line while the recorded click boxes
    stay where they were drawn.
    """
    try:
        columns = shutil.get_terminal_size((80, 24)).columns
    except Exception:
        columns = 80
    return max(_MIN_WIDTH, columns - 1)


def plain(text):
    """The visible text, with the colour escapes taken out.

    Everything that lines a column up has to measure this rather than the
    finished string: an escape sequence occupies characters but no width.
    """
    return _ANSI_RE.sub("", text)


def short_label(key, value):
    """Compact name for the settings grid."""
    if key == "resolution":
        return {"best": "Best", "worst": "Worst"}.get(value, f"{value}p")
    if key == "mode":
        return {"video": "Video / Image", "audio": "Audio"}.get(value, value)
    if key == "auto_update":
        return "On" if value else "Off"
    return str(value).upper()


def format_status(cfg):
    """One-line summary of the format settings, for logs and the fallback menu."""
    if cfg.get("mode", "video") == "audio":
        return f"Audio ({cfg.get('audio_format', 'mp3').upper()})"
    res = short_label("resolution", cfg.get("resolution", "best"))
    return f"Video / Image ({cfg.get('video_format', 'mp4').upper()} @ {res})"


def settings_rows(cfg):
    """(heading, config key, [(value, label), ...]) for the current mode.

    The mode row decides what the rows below it contain: audio has its own
    format list and no resolution at all, so switching mode reshapes the screen
    instead of leaving dead options on it.
    """
    rows = [("Mode", "mode",
             [(v, short_label("mode", v)) for v in ("video", "audio")])]

    if cfg.get("mode", "video") == "audio":
        rows.append(("File format", "audio_format",
                     [(v, short_label("audio_format", v)) for v, _ in AUDIO_FORMATS]))
    else:
        rows.append(("File format", "video_format",
                     [(v, short_label("video_format", v)) for v, _ in VIDEO_FORMATS]))
        rows.append(("Resolution", "resolution",
                     [(v, short_label("resolution", v)) for v, _ in RESOLUTION_OPTIONS]))

    rows.append(("Auto-update", "auto_update", [(True, "On"), (False, "Off")]))
    return rows


def navigable_rows(cfg):
    """Every row Up/Down can land on: the settings, then the actions."""
    rows = [("settings", key, [v for v, _ in options])
            for _, key, options in settings_rows(cfg)]
    rows += [("action", name, None) for _, name, _, _ in ACTIONS]
    return rows


def build_layout(cfg, focus_row=0, focus_col=0, login="none saved",
                 interactive=True, width=None, with_banner=True):
    """Render the whole screen and record where everything landed.

    Returns the lines to draw plus the click map. The banner is part of the
    layout so the y coordinates in that map are absolute screen rows — the
    interactive menu repaints from the top of the window, so row 0 of the
    layout is row 0 of the terminal. The typed fallback scrolls instead of
    repainting and has already printed the banner once, so it turns this off.
    """
    width = width or screen_width()
    lines = list(banner_lines()) if with_banner else []
    hits = []
    rows = navigable_rows(cfg)
    focus_row = max(0, min(focus_row, len(rows) - 1))

    def cursor(is_focused):
        return f"{C.CYAN}›{C.RESET} " if is_focused else "  "

    lines.append("")
    lines.append(f"{_INDENT}{C.BOLD}{C.CYAN}DOWNLOAD SETTINGS{C.RESET}")

    row_index = 0
    for heading, key, options in settings_rows(cfg):
        focused = interactive and row_index == focus_row
        current = cfg.get(key)
        prefix = f"{_INDENT}{cursor(focused)}{heading:<{_LABEL_W}}"
        indent = len(plain(prefix))
        line = prefix
        column = indent

        for col, (value, label) in enumerate(options):
            selected = value == current
            mark = (f"{C.GREEN}{MARK_ON}{C.RESET}" if selected
                    else f"{C.DIM}{MARK_OFF}{C.RESET}")
            body = f"{mark} {label}"
            if focused and col == focus_col:
                body = f"{mark} {C.REVERSE}{label}{C.RESET}"
            elif selected:
                body = f"{mark} {C.WHITE}{label}{C.RESET}"

            cell_w = len(plain(body))
            # Wrap under the label rather than letting the terminal do it, so
            # the click boxes and the drawn text agree about which line is
            # which. Always place at least one option per line.
            if column > indent and column + cell_w > width:
                lines.append(line.rstrip())
                line = " " * indent
                column = indent

            hits.append(Hit(len(lines), column, column + cell_w - 1,
                            ("set", key, value), row_index, col))
            line += body + "   "
            column += cell_w + 3

        lines.append(line.rstrip())
        row_index += 1

    lines.append("")
    lines.append(f"{_INDENT}{C.DIM}{'─' * 56}{C.RESET}")

    for number, name, label, hint in ACTIONS:
        focused = interactive and row_index == focus_row
        prefix = f"{_INDENT}{cursor(focused)}"
        body = f"{C.GREEN}[{number}]{C.RESET} "
        text = f"{C.REVERSE}{label}{C.RESET}" if focused else label
        body += text
        if hint:
            body += f"  {C.DIM}{hint}{C.RESET}"

        x0 = len(plain(prefix))
        hits.append(Hit(len(lines), x0, x0 + len(plain(body)) - 1,
                        ("action", name, None), row_index, 0))
        lines.append(prefix + body)
        row_index += 1

    lines.append(f"{_INDENT}{C.DIM}{'─' * 56}{C.RESET}")
    out = str(cfg.get("output_dir", ""))
    room = width - len(_INDENT) - len("Output:   ")
    if len(out) > room > 3:
        out = "..." + out[-(room - 3):]
    lines.append(f"{_INDENT}{C.DIM}Output:   {out}{C.RESET}")
    lines.append(f"{_INDENT}{C.DIM}Platform: {OS_LABEL} ({PLATFORM_TAG})"
                 f"    Login: {login}{C.RESET}")
    lines.append("")

    if interactive:
        row_kind = rows[focus_row][0] if rows else "action"
        keys = ("↑↓ move   ←→ change   Enter next   click anything   0 exit"
                if row_kind == "settings"
                else "↑↓ move   Enter run   number keys work too   0 exit")
        lines.append(f"{_INDENT}{C.DIM}{keys}{C.RESET}")

    return Layout(lines, hits, rows)


def hit_test(layout, x, y):
    """The Hit under this click, or None if the click landed on nothing."""
    for hit in layout.hits:
        if hit.y == y and hit.x0 <= x <= hit.x1:
            return hit
    return None
