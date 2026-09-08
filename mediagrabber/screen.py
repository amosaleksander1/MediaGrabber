"""Raw-mode terminal input and in-place drawing.

The menu is a screen you move around with the arrow keys and click on, rather
than a list of numbers that scrolls away every time you touch it. That needs
three things a normal terminal does not give you by default: keys delivered one
at a time instead of a line at a time, mouse clicks reported as events, and a
way to repaint the same rows instead of appending new ones.

Two implementations, because the two families disagree completely:

* Windows reads console *records* through ReadConsoleInputW, which carries key
  and mouse events natively in both conhost and Windows Terminal. Getting mouse
  events means clearing ENABLE_QUICK_EDIT_MODE — the same flag that lets a user
  select text with the mouse — so raw mode is entered only while the menu is up
  and always restored afterwards.
* Everything else uses termios raw mode plus the DEC/SGR mouse escapes, and
  parses the sequences by hand.

None of this exists when the app is piped (CI smoke tests, ``printf | app``),
so ``is_interactive()`` is the gate and the caller keeps a typed-number menu for
that case. A frozen binary double-clicked on Windows is interactive; the same
binary in a pipeline is not.
"""

import os
import sys
from collections import namedtuple

from .platform_support import IS_WIN

#: kind is "key", "mouse" or "resize".
#: name is "up"/"down"/"left"/"right"/"enter"/"space"/"escape"/"quit" or a
#: single character. x/y are 0-based columns/rows from the top-left of the
#: visible window, and only mean anything for a mouse event.
Event = namedtuple("Event", "kind name x y")

KEY_NAMES = {"up", "down", "left", "right", "enter", "space", "escape"}


def is_interactive():
    """Can this process own the terminal? False when piped or redirected."""
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except Exception:
        return False


# ── WINDOWS ──────────────────────────────────────────────────────────────────

if IS_WIN:
    import ctypes
    from ctypes import wintypes

    STD_INPUT = -10
    STD_OUTPUT = -11

    ENABLE_PROCESSED_INPUT = 0x0001   # keeps Ctrl+C working
    ENABLE_LINE_INPUT = 0x0002
    ENABLE_ECHO_INPUT = 0x0004
    ENABLE_MOUSE_INPUT = 0x0010
    ENABLE_QUICK_EDIT = 0x0040
    ENABLE_EXTENDED_FLAGS = 0x0080

    KEY_EVENT = 0x0001
    MOUSE_EVENT = 0x0002

    VK = {0x26: "up", 0x28: "down", 0x25: "left", 0x27: "right",
          0x0D: "enter", 0x1B: "escape", 0x20: "space"}

    class _COORD(ctypes.Structure):
        _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]

    class _KEY(ctypes.Structure):
        _fields_ = [("bKeyDown", wintypes.BOOL),
                    ("wRepeatCount", wintypes.WORD),
                    ("wVirtualKeyCode", wintypes.WORD),
                    ("wVirtualScanCode", wintypes.WORD),
                    ("UnicodeChar", ctypes.c_wchar),
                    ("dwControlKeyState", wintypes.DWORD)]

    class _MOUSE(ctypes.Structure):
        _fields_ = [("dwMousePosition", _COORD),
                    ("dwButtonState", wintypes.DWORD),
                    ("dwControlKeyState", wintypes.DWORD),
                    ("dwEventFlags", wintypes.DWORD)]

    class _EVENT(ctypes.Union):
        _fields_ = [("KeyEvent", _KEY), ("MouseEvent", _MOUSE),
                    ("pad", ctypes.c_byte * 16)]

    class _RECORD(ctypes.Structure):
        _fields_ = [("EventType", wintypes.WORD), ("Event", _EVENT)]

    class _SMALL_RECT(ctypes.Structure):
        _fields_ = [("Left", ctypes.c_short), ("Top", ctypes.c_short),
                    ("Right", ctypes.c_short), ("Bottom", ctypes.c_short)]

    class _SCREEN_INFO(ctypes.Structure):
        _fields_ = [("dwSize", _COORD), ("dwCursorPosition", _COORD),
                    ("wAttributes", wintypes.WORD), ("srWindow", _SMALL_RECT),
                    ("dwMaximumWindowSize", _COORD)]


class Screen:
    """Raw keyboard + mouse for as long as the menu is on screen.

    Always used as a context manager: leaving it restores the console, and the
    caller must leave it before running a download, so scrolling log output and
    the Q-to-cancel check (which expect a cooked terminal) behave normally.
    """

    def __init__(self):
        self._entered = False
        self._saved_mode = None
        self._saved_term = None
        self._in_handle = None

    # -- lifecycle ---------------------------------------------------------

    def __enter__(self):
        if IS_WIN:
            self._enter_windows()
        else:
            self._enter_posix()
        self._entered = True
        sys.stdout.write("\033[?25l")          # hide the cursor
        sys.stdout.flush()
        return self

    def __exit__(self, *exc):
        # Restoring matters more than anything that failed above: a console
        # left raw has no echo and no line editing.
        try:
            sys.stdout.write("\033[?25h")      # show the cursor
            sys.stdout.flush()
        except Exception:
            pass
        try:
            if IS_WIN:
                self._exit_windows()
            else:
                self._exit_posix()
        except Exception:
            pass
        self._entered = False
        return False

    def _enter_windows(self):
        self._in_handle = ctypes.windll.kernel32.GetStdHandle(STD_INPUT)
        mode = wintypes.DWORD()
        ctypes.windll.kernel32.GetConsoleMode(self._in_handle, ctypes.byref(mode))
        self._saved_mode = mode.value
        new = (mode.value & ~ENABLE_LINE_INPUT & ~ENABLE_ECHO_INPUT
               & ~ENABLE_QUICK_EDIT)
        new |= ENABLE_MOUSE_INPUT | ENABLE_EXTENDED_FLAGS | ENABLE_PROCESSED_INPUT
        ctypes.windll.kernel32.SetConsoleMode(self._in_handle, new)

    def _exit_windows(self):
        if self._saved_mode is not None and self._in_handle is not None:
            ctypes.windll.kernel32.SetConsoleMode(self._in_handle, self._saved_mode)
            self._saved_mode = None

    def _enter_posix(self):
        import termios
        import tty
        fd = sys.stdin.fileno()
        self._saved_term = termios.tcgetattr(fd)
        tty.setraw(fd)
        # 1000 = click reporting, 1006 = SGR coordinates (works past column 95).
        sys.stdout.write("\033[?1000h\033[?1006h")
        sys.stdout.flush()

    def _exit_posix(self):
        import termios
        sys.stdout.write("\033[?1000l\033[?1006l")
        sys.stdout.flush()
        if self._saved_term is not None:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN,
                              self._saved_term)
            self._saved_term = None

    # -- drawing -----------------------------------------------------------

    def render(self, lines):
        """Repaint from the top of the window instead of appending.

        The newline goes *between* lines, never after the last one: a newline
        on the bottom row of the window scrolls the whole screen up, which
        would put layout row 0 above the top of the window and leave every
        recorded click box pointing at the wrong text.
        """
        body = "\033[K\r\n".join(lines)        # erase the rest of each row
        sys.stdout.write("\033[H" + body + "\033[K\033[J")
        sys.stdout.flush()

    # -- input -------------------------------------------------------------

    def read_event(self):
        """Block until something happens. Returns an Event, or None to ignore."""
        return self._read_windows() if IS_WIN else self._read_posix()

    def _window_top(self):
        """Buffer row currently at the top of the window (Windows scrollback)."""
        try:
            info = _SCREEN_INFO()
            handle = ctypes.windll.kernel32.GetStdHandle(STD_OUTPUT)
            if ctypes.windll.kernel32.GetConsoleScreenBufferInfo(
                    handle, ctypes.byref(info)):
                return info.srWindow.Top
        except Exception:
            pass
        return 0

    def _read_windows(self):
        record = _RECORD()
        count = wintypes.DWORD()
        while True:
            ok = ctypes.windll.kernel32.ReadConsoleInputW(
                self._in_handle, ctypes.byref(record), 1, ctypes.byref(count))
            if not ok or count.value == 0:
                return None

            if record.EventType == KEY_EVENT:
                key = record.Event.KeyEvent
                if not key.bKeyDown:
                    continue
                name = VK.get(key.wVirtualKeyCode)
                if name:
                    return Event("key", name, 0, 0)
                ch = key.UnicodeChar
                if ch and ch.isprintable():
                    return Event("key", ch.lower(), 0, 0)
                continue

            if record.EventType == MOUSE_EVENT:
                mouse = record.Event.MouseEvent
                # dwEventFlags 0 is a plain press/release; ignore moves and
                # double-click duplicates so one click is one event.
                if mouse.dwEventFlags == 0 and (mouse.dwButtonState & 0x1):
                    pos = mouse.dwMousePosition
                    return Event("mouse", "left", pos.X,
                                 pos.Y - self._window_top())
                continue

    def _read_posix(self):
        fd = sys.stdin.fileno()
        data = os.read(fd, 1)
        if not data:
            return None
        if data != b"\x1b":
            return _plain_key(data)

        # An escape sequence arrives in one burst; a lone Esc does not.
        rest = b""
        try:
            import select
            while select.select([fd], [], [], 0.02)[0]:
                chunk = os.read(fd, 32)
                if not chunk:
                    break
                rest += chunk
                if rest[-1:] in (b"M", b"m", b"A", b"B", b"C", b"D", b"~"):
                    break
        except Exception:
            pass
        return decode_escape(rest)


def _plain_key(data):
    if data in (b"\r", b"\n"):
        return Event("key", "enter", 0, 0)
    if data == b" ":
        return Event("key", "space", 0, 0)
    if data == b"\x03":                        # Ctrl+C
        raise KeyboardInterrupt
    try:
        ch = data.decode("utf-8", "ignore")
    except Exception:
        return None
    return Event("key", ch.lower(), 0, 0) if ch.isprintable() else None


def decode_escape(rest):
    """Turn the tail of an escape sequence into an Event.

    Split out so the parser can be tested without a terminal: the arrow keys
    and the SGR mouse report are the two shapes that matter.
    """
    if not rest:
        return Event("key", "escape", 0, 0)
    text = rest.decode("latin-1", "ignore")

    if text.startswith("[<"):
        # SGR mouse: ESC [ < button ; col ; row (M press | m release)
        body = text[2:]
        final = body[-1:]
        try:
            button, col, row = (int(p) for p in body[:-1].split(";"))
        except ValueError:
            return None
        if final != "M" or button & 0b11 != 0:   # left button press only
            return None
        return Event("mouse", "left", col - 1, row - 1)

    arrows = {"[A": "up", "[B": "down", "[C": "right", "[D": "left",
              "OA": "up", "OB": "down", "OC": "right", "OD": "left"}
    for prefix, name in arrows.items():
        if text.startswith(prefix):
            return Event("key", name, 0, 0)
    return None
