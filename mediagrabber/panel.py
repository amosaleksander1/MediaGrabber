"""Driving a screen of radio rows and actions.

The main menu, the browser picker and the first-run wizard are all the same
thing underneath: rows you move between, options you pick, actions you run.
This is the loop that drives them and the one handler that decides what a
keypress or a click means, so a click behaves identically wherever it lands.

Layout lives in ``menu``, which stays free of terminal code; raw input lives in
``screen``. This is the only module that needs both.
"""

from .menu import build_panel, hit_test
from .screen import Screen, is_interactive
from .ui import C


def handle_event(layout, focus, event, get_current, set_value,
                 number_actions=(), escape_action="cancel"):
    """Apply one keypress or click. Returns an action name, or None to redraw.

    ``get_current(key)`` reads the selected value of a radio row and
    ``set_value(key, value)`` changes it — passed in rather than assumed, so
    the same handler serves a screen backed by the config file and one backed
    by a dict the caller throws away. ``escape_action`` is what Escape means
    here: leaving the app on the main menu, abandoning a panel anywhere else.
    """
    rows = layout.rows
    if not rows:
        return None
    focus["row"] = max(0, min(focus["row"], len(rows) - 1))
    row = rows[focus["row"]]

    def column_of(r):
        _, key, values = r
        try:
            return values.index(get_current(key))
        except ValueError:
            return 0

    if event.kind == "mouse":
        hit = hit_test(layout, event.x, event.y)
        if hit is None:
            return None
        focus["row"], focus["col"] = hit.row, hit.col
        kind, first, second = hit.target
        if kind == "set":
            set_value(first, second)
            return None
        return first

    name = event.name

    if name in ("up", "down"):
        step = -1 if name == "up" else 1
        focus["row"] = (focus["row"] + step) % len(rows)
        moved = rows[focus["row"]]
        focus["col"] = column_of(moved) if moved[0] == "settings" else 0
        return None

    if row[0] == "settings":
        _, key, values = row
        if name in ("left", "right", "enter", "space") and values:
            step = -1 if name == "left" else 1
            focus["col"] = (column_of(row) + step) % len(values)
            set_value(key, values[focus["col"]])
            return None
    elif name in ("enter", "space"):
        return row[1]

    if name in ("escape", "q"):
        return escape_action

    for number, action in number_actions:
        if name == number:
            return action

    return None


def run_panel(title, note, groups, values, actions, on_change=None):
    """Show one panel and drive it until an action is picked.

    ``values`` is updated in place as the user chooses, and ``on_change`` is
    called with (key, value) if the caller needs to react — persisting the
    choice, say. Returns the chosen action's name, or "cancel" for Escape.

    Requires a real terminal; callers check ``is_interactive()`` first and
    offer something typed when there is not one.
    """
    focus = {"row": 0, "col": 0}

    def setter(key, value):
        values[key] = value
        if on_change:
            on_change(key, value)

    with Screen() as scr:
        while True:
            layout = build_panel(title, note, groups, actions, values,
                                 focus["row"], focus["col"])
            scr.render(layout.lines)
            event = scr.read_event()
            if event is None:
                continue
            action = handle_event(layout, focus, event, values.get, setter)
            if action:
                return action


def confirm(question, note="", default_no=True):
    """A yes/no question as a panel, so it looks like the rest of the app."""
    if not is_interactive():
        return False
    values = {"answer": False if default_no else True}
    groups = [("Choose", "answer", [(True, "Yes"), (False, "No")])]
    actions = [("ok", "Confirm", ""), ("cancel", "Cancel", "")]
    result = run_panel(question, note, groups, values, actions)
    return result == "ok" and bool(values.get("answer"))


def say(*lines):
    """Print a few plain lines between panels, outside raw mode."""
    print()
    for line in lines:
        print(f"  {line}")
    print()


def pause(message="Press Enter to continue..."):
    try:
        input(f"  {C.DIM}{message}{C.RESET}")
    except (EOFError, KeyboardInterrupt):
        pass
