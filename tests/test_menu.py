#!/usr/bin/env python3
"""The interactive menu's two testable halves: input decoding and hit-testing.

Whether clicking *feels* right can only be judged by a person at a terminal.
What can be checked here is everything underneath that:

  * escape sequences decode to the right event — arrows and the SGR mouse
    report, which is the only shape that carries coordinates;
  * every option drawn on the screen can be clicked back to the exact setting
    it displays, at both edges of its box. A layout change that shifts a column
    without shifting its recorded box makes clicks land on the neighbour, and
    nothing about the screen would look wrong;
  * moving and selecting does what the row type promises, including that no
    key is dead on a focused row;
  * the piped fallback still reaches every action, since CI drives the app
    that way and never sees the interactive path at all.

Run:  python3 tests/test_menu.py
"""

import os
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover — exotic stream
        pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mediagrabber import app                                    # noqa: E402
from mediagrabber.config import DEFAULTS                        # noqa: E402
from mediagrabber.menu import (ACTIONS, build_layout, build_panel,  # noqa: E402
                               hit_test, navigable_rows, plain,
                               settings_rows)
from mediagrabber.screen import Event, decode_escape             # noqa: E402


def _cfg(**over):
    cfg = dict(DEFAULTS)
    cfg.update(over)
    return cfg


def check_decoding(fail):
    for raw, name in ((b"[A", "up"), (b"[B", "down"),
                      (b"[C", "right"), (b"[D", "left"),
                      (b"OA", "up"), (b"OB", "down")):
        got = decode_escape(raw)
        if got != Event("key", name, 0, 0):
            fail(f"escape {raw!r} decoded to {got!r}, expected key {name}")

    if decode_escape(b"") != Event("key", "escape", 0, 0):
        fail("a bare Esc must decode as escape")

    # SGR mouse press: ESC [ < button ; column ; row M, all 1-based.
    got = decode_escape(b"[<0;12;7M")
    if got != Event("mouse", "left", 11, 6):
        fail(f"SGR press decoded to {got!r}, expected mouse at (11, 6)")

    # A release ('m') and the other buttons must not act as a click, or every
    # click fires twice and right-clicking picks something.
    for raw, why in ((b"[<0;12;7m", "button release"),
                     (b"[<2;12;7M", "right button"),
                     (b"[<1;12;7M", "middle button")):
        if decode_escape(raw) is not None:
            fail(f"{why} must not decode as a click")

    if decode_escape(b"[<garbage;M") is not None:
        fail("an unparseable mouse report must be ignored, not raise")


def check_hit_boxes(fail):
    """Every drawn option must click back to itself, at both of its edges.

    Widths are pinned rather than detected: the point is that the map holds at
    a width where rows have to wrap (ten audio formats never fit on one line),
    and a test that inherits the runner's terminal size would not reliably
    exercise that.
    """
    for width in (60, 80, 120):
        for cfg in (_cfg(), _cfg(mode="audio")):
            _check_one_layout(fail, cfg, width)


def _check_one_layout(fail, cfg, width):
        where = f"[{cfg['mode']} @ {width} cols]"
        layout = build_layout(cfg, login="none saved", width=width)

        if len(layout.rows) != len(navigable_rows(cfg)):
            fail("the layout's row list disagrees with navigable_rows()")

        # Nothing may run past the width the layout was given, or the terminal
        # wraps it and every row below is drawn one line lower than its map.
        for y, line in enumerate(layout.lines):
            if len(plain(line)) > width:
                fail(f"{where} line {y} is {len(plain(line))} wide, over {width}")

        for want in layout.hits:
            for x in (want.x0, want.x1):
                got = hit_test(layout, x, want.y)
                if got is None:
                    fail(f"{where} click at ({x}, {want.y}) hit nothing, "
                         f"expected {want.target}")
                elif got.target != want.target:
                    fail(f"{where} click at ({x}, {want.y}) resolved to "
                         f"{got.target}, expected {want.target}")

        # Boxes on the same line must not overlap, or the one found first wins
        # and its neighbour becomes unclickable.
        by_line = {}
        for hit in layout.hits:
            by_line.setdefault(hit.y, []).append(hit)
        for y, hits in by_line.items():
            ordered = sorted(hits, key=lambda h: h.x0)
            for left, right in zip(ordered, ordered[1:]):
                if left.x1 >= right.x0:
                    fail(f"{where} line {y}: {left.target} overlaps "
                         f"{right.target}")

        # Nothing may be drawn off the left edge, and a box must never claim
        # width the visible text does not have.
        for hit in layout.hits:
            if hit.x0 < 0 or hit.x1 < hit.x0:
                fail(f"{where} {hit.target} has an impossible box "
                     f"({hit.x0}..{hit.x1})")
            if hit.y >= len(layout.lines):
                fail(f"{where} {hit.target} is on a line that is not drawn")
            elif hit.x1 >= len(plain(layout.lines[hit.y])):
                fail(f"{where} {hit.target} claims more width than its line has")

        for _, name, _, _ in ACTIONS:
            if not any(h.target == ("action", name, None) for h in layout.hits):
                fail(f"{where} action {name} is not clickable")

        # Every option the config can hold must be reachable, or a value
        # becomes impossible to select once something else is chosen.
        for _, key, options in settings_rows(cfg):
            for value, _ in options:
                if not any(h.target == ("set", key, value) for h in layout.hits):
                    fail(f"{where} {key}={value!r} is drawn but not clickable")


def check_fits_the_window(fail):
    """A layout taller than the window scrolls, and scrolling breaks clicking.

    Once the screen scrolls mid-render, layout row 0 is no longer the top of
    the window and every click box points at the wrong text. 80x25 is the
    classic console size and the settings alone are most of it, so this is a
    real configuration, not a corner case.
    """
    for height in (25, 30, 40):
        for cfg in (_cfg(), _cfg(mode="audio")):
            for problem in (None, "Startup found problems — see the log"):
                layout = build_layout(cfg, width=80, height=height,
                                      problem=problem)
                if len(layout.lines) > height:
                    fail(f"[{cfg['mode']} @ {height} rows] layout is "
                         f"{len(layout.lines)} lines and would scroll")
                # Trimming must not cost anything you have to click.
                for _, name, _, _ in ACTIONS:
                    if not any(h.target == ("action", name, None)
                               for h in layout.hits):
                        fail(f"[{cfg['mode']} @ {height} rows] trimming lost "
                             f"the {name} action")
                for _, key, options in settings_rows(cfg):
                    for value, _ in options:
                        if not any(h.target == ("set", key, value)
                                   for h in layout.hits):
                            fail(f"[{cfg['mode']} @ {height} rows] trimming "
                                 f"lost {key}={value!r}")

    # A problem found at startup has to reach the screen: it is logged before
    # the menu opens, and the first repaint would paint straight over it.
    warned = build_layout(_cfg(), width=80, problem="disk on fire")
    if not any("disk on fire" in plain(line) for line in warned.lines):
        fail("a startup problem must be visible on the menu itself")


def check_mode_reshapes(fail):
    """Audio has no resolution, so that row must leave rather than go dead."""
    keys = [key for _, key, _ in settings_rows(_cfg(mode="audio"))]
    if "resolution" in keys:
        fail("audio mode must not offer a resolution row")
    if "video_format" in keys:
        fail("audio mode must not offer the video format list")

    keys = [key for _, key, _ in settings_rows(_cfg(mode="video"))]
    for expected in ("mode", "video_format", "resolution", "auto_update"):
        if expected not in keys:
            fail(f"video mode is missing the {expected} row")


def check_navigation(fail):
    """The controller: arrows move, and no key is dead on a focused row."""
    saved = []
    original = app.save_config
    app.save_config = saved.append
    try:
        cfg = _cfg()
        focus = {"row": 0, "col": 0}
        layout = build_layout(cfg, focus["row"], focus["col"])

        # Right on the mode row changes the value immediately.
        app._handle_event(cfg, layout, focus, Event("key", "right", 0, 0))
        if cfg["mode"] != "audio":
            fail("right on the mode row should have selected audio")
        if not saved:
            fail("changing a setting must persist it")

        # Enter on a settings row cycles rather than doing nothing.
        cfg = _cfg()
        focus = {"row": 0, "col": 0}
        layout = build_layout(cfg, 0, 0)
        app._handle_event(cfg, layout, focus, Event("key", "enter", 0, 0))
        if cfg["mode"] == "video":
            fail("enter on a settings row must move to the next option")

        # Down from the last row wraps to the first, and the column follows the
        # value that row currently holds instead of keeping a stale index.
        cfg = _cfg(resolution="720")
        focus = {"row": len(layout.rows) - 1, "col": 0}
        layout = build_layout(cfg, focus["row"], focus["col"])
        app._handle_event(cfg, layout, focus, Event("key", "down", 0, 0))
        if focus["row"] != 0:
            fail("down from the last row must wrap to the first")

        # A number key runs its action from anywhere.
        got = app._handle_event(cfg, layout, focus, Event("key", "1", 0, 0))
        if got != "single":
            fail(f"pressing 1 returned {got!r}, expected 'single'")
        got = app._handle_event(cfg, layout, focus, Event("key", "escape", 0, 0))
        if got != "exit":
            fail("escape must leave the app")

        # A click on an option applies it and moves the focus there.
        cfg = _cfg()
        focus = {"row": 0, "col": 0}
        layout = build_layout(cfg, 0, 0)
        target = next(h for h in layout.hits
                      if h.target == ("set", "resolution", "720"))
        app._handle_event(cfg, layout, focus,
                          Event("mouse", "left", target.x0, target.y))
        if cfg["resolution"] != "720":
            fail("clicking a resolution option must select it")
        if (focus["row"], focus["col"]) != (target.row, target.col):
            fail("clicking must move the focus to what was clicked")

        # A click on empty space must do nothing at all.
        before = dict(cfg)
        app._handle_event(cfg, layout, focus, Event("mouse", "left", 200, 0))
        if cfg != before:
            fail("a click that hits nothing must not change anything")
    finally:
        app.save_config = original


def check_fallback(fail):
    """CI pipes input, so the typed path must reach every action."""
    numbers = [number for number, _, _, _ in ACTIONS]
    if len(set(numbers)) != len(numbers):
        fail("two actions share a number key")
    if "0" not in numbers:
        fail("0 must still exit")

    order = [name for _, name, _, _ in ACTIONS]
    if order[:3] != ["single", "batch", "edit"]:
        fail(f"menu order is {order[:3]}, expected single, batch, edit first")

    # The non-interactive layout must not draw a focus indicator, which would
    # be a permanent highlight on a screen nobody can move.
    lines = build_layout(_cfg(), interactive=False).lines
    if any("\033[7m" in line for line in lines):
        fail("the piped menu must not render a focus highlight")



def check_panels(fail):
    """The wizard and the browser picker are a second layout, so guard them too.

    They share the row emitters with the main menu, which is the whole point of
    that sharing — but they arrange their own lines, so the click map can still
    come apart independently. The browser picker is the one that matters: nine
    options wrap onto several rows at any normal width, and a wrap the layout
    did not account for is exactly what sends a click to the wrong option.
    """
    from mediagrabber.firstrun import _browser_options

    options, _ = _browser_options()
    if len(options) < 8:
        fail(f"expected the browser picker to wrap; only {len(options)} options")

    groups = [("Browser", "cookies_browser", options)]
    actions = [("ok", "Use this browser", ""), ("cancel", "Skip for now", "")]
    note = ("A note long enough to wrap onto a second line so the layout has "
            "to account for it when it decides what fits on this screen.")

    for width in (60, 80, 120):
        for height in (25, 40):
            where = f"[panel {width}x{height}]"
            panel = build_panel("Which browser do you use?", note, groups,
                                actions, {"cookies_browser": "zen"},
                                width=width, height=height)

            for y, line in enumerate(panel.lines):
                if len(plain(line)) > width:
                    fail(f"{where} line {y} is {len(plain(line))} wide")

            for want in panel.hits:
                for x in (want.x0, want.x1):
                    got = hit_test(panel, x, want.y)
                    if got is None or got.target != want.target:
                        fail(f"{where} click ({x}, {want.y}) gave "
                             f"{got.target if got else None}, "
                             f"wanted {want.target}")

            by_line = {}
            for hit in panel.hits:
                by_line.setdefault(hit.y, []).append(hit)
            for y, hits in by_line.items():
                ordered = sorted(hits, key=lambda h: h.x0)
                for left, right in zip(ordered, ordered[1:]):
                    if left.x1 >= right.x0:
                        fail(f"{where} line {y}: {left.target} overlaps "
                             f"{right.target}")

            # Every browser must remain selectable, and both actions reachable.
            for value, _ in options:
                if not any(h.target == ("set", "cookies_browser", value)
                           for h in panel.hits):
                    fail(f"{where} {value!r} is drawn but not clickable")
            for name, _, _ in actions:
                if not any(h.target == ("action", name, None)
                           for h in panel.hits):
                    fail(f"{where} action {name} is unreachable")

    # The rows a panel reports must match what it drew, or the arrow keys walk
    # a different list from the one on screen.
    panel = build_panel("t", "", groups, actions, {"cookies_browser": "zen"},
                        width=80, height=40)
    kinds = [r[0] for r in panel.rows]
    if kinds != ["settings", "action", "action"]:
        fail(f"panel rows are {kinds}, expected one settings row then two actions")


def main():
    failures = []
    fail = failures.append

    check_decoding(fail)
    check_hit_boxes(fail)
    check_fits_the_window(fail)
    check_panels(fail)
    check_mode_reshapes(fail)
    check_navigation(fail)
    check_fallback(fail)

    print("Checked escape/mouse decoding, every option's click box in both "
          "modes at three widths, fitting into a 25-row window, row "
          "reshaping, focus movement, the wizard/browser panels and the "
          "piped fallback.")
    print("=" * 60)
    if failures:
        print("FAILURES:")
        for f in failures:
            print("  x " + f)
        return 1
    print("The menu's input decoding and click map behave correctly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
