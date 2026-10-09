"""The first-run wizard.

Someone opening MediaGrabber for the first time has three things to decide and
no idea that any of them exist: where downloads go, which browser holds the
login it should borrow, and whether the tools it needs are present. Left to
discover those through the menu, the usual first experience is an Instagram
link that fails with an authentication error.

So this asks, once, in the order the answers matter, and every screen says why
it is asking rather than only what it wants. Esc steps back to the previous
question rather than abandoning the lot.
"""

from pathlib import Path

from .config import OUTPUT_DIR, save_config
from .cookies import browser_order, detect_installed_browsers
from .panel import confirm, run_panel
from .platform_support import (copy_path_hint, normalise_pasted_path,
                               open_path, pick_folder_dialog)
from .screen import is_interactive, read_line
from .tools import needs_system_python, offer_python_install
from .ui import C, log


def needs_setup(cfg):
    """Has the wizard ever been completed?

    An explicit marker rather than "does config.json exist": the file is
    written on the very first startup, before the user has answered anything,
    so its presence proves nothing. Anyone can re-run the wizard from the menu,
    which is why this is a stored answer and not a one-time guess.
    """
    return not cfg.get("setup_done", False)


def _browser_options():
    """Installed browsers first, each marked so the choice is obvious."""
    found = detect_installed_browsers()
    options = [(b, f"{b.title()}{' (found)' if b in found else ''}")
               for b in browser_order()]
    options.append(("auto", "Decide for me"))
    options.append(("none", "Skip - public links only"))
    return options, found


def choose_browser(cfg, first_run=False):
    """Which browser's login to borrow.

    Phrased around what the user did, not what the app does: nobody thinks of
    it as "a cookie source", they think of it as the browser they are logged
    into Instagram on.

    Returns the browser picked, "" when skipped, or None when Esc backed out.
    """
    options, found = _browser_options()
    note = ("MediaGrabber borrows the login from a browser you are already "
            "signed in on, so it can open posts that require an account. It "
            "never sees or stores your password. Pick the browser you use for "
            "Instagram or TikTok.")
    if found:
        note += f"  Found on this computer: {', '.join(b.title() for b in found)}."

    if not is_interactive():
        from .cookies import choose_cookie_browser
        return choose_cookie_browser(cfg) or ""

    values = {"cookies_browser": cfg.get("cookies_browser", "auto")}
    groups = [("Browser", "cookies_browser", options)]
    actions = [("ok", "Use this browser", ""),
               ("skip", "Skip for now", "")]

    result = run_panel("Which browser do you use to log into Instagram?",
                       note, groups, values, actions)
    if result == "cancel":
        return None
    if result != "ok":
        return ""

    picked = values["cookies_browser"]
    cfg["cookies_browser"] = picked
    save_config(cfg)
    log(f"Login will be read from: {picked}", "OK")

    # Exporting the cookies is slow and can prompt, so it is not done during
    # the wizard; the checkup picks it up on the next run.
    if not first_run and picked != "none":
        from .cookies import COOKIES_FILE, refresh_cookie_cache
        try:
            COOKIES_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        refresh_cookie_cache(cfg)
    return picked


def ask_output_folder(cfg, first_run=False):
    """Where downloads land, with the paste trick spelled out.

    Shared by the wizard and the menu's Change Output Folder, so the two can
    never offer different choices. Typing a path by hand is the step people
    get wrong, so pasting is first and its instructions are for the thing they
    will actually do: copy it from the file manager.

    Returns a one-line summary of the outcome, or None when Esc backed out.
    """
    problem = ""
    while True:
        current = cfg.get("output_dir", OUTPUT_DIR)
        note = f"Downloads currently go to: {current}"
        if problem:
            note += f"   ! {problem}"
        keep = "Keep this folder" + (" and continue" if first_run else "")
        actions = [("paste", "Paste a folder path", "copied from your file manager", "1"),
                   ("browse", "Browse with a folder picker", "", "2"),
                   ("default", "Reset to the default folder", "", "3"),
                   ("keep", keep, "", "0")]
        choice = run_panel("Where should downloads be saved?", note, [], {},
                           actions)

        if choice == "cancel":
            return None
        if choice == "keep":
            return f"Downloads go to: {current}"
        if choice == "default":
            cfg["output_dir"] = OUTPUT_DIR
            save_config(cfg)
            log(f"Output folder reset to: {OUTPUT_DIR}", "OK")
            return f"Output folder reset to the default: {OUTPUT_DIR}"
        if choice == "browse":
            picked = pick_folder_dialog(current)
            if not picked:
                problem = "No folder chosen - try pasting the path instead."
                continue
            problem = _set_output(cfg, Path(picked))
        else:
            problem = _paste_output(cfg)
        if problem is None:
            return f"Downloads will be saved to: {cfg['output_dir']}"


def _paste_output(cfg):
    """Read a pasted path. None on success, otherwise what went wrong."""
    print()
    print(f"  {C.BOLD}How to copy a folder's path{C.RESET}")
    print(f"  {C.DIM}{copy_path_hint()}{C.RESET}")
    print(f"  {C.DIM}Quotes are fine - paste exactly what you copied. "
          f"Esc goes back.{C.RESET}")
    print()
    typed = read_line(f"  {C.CYAN}Paste the folder path:{C.RESET} ")
    if typed is None:
        return ""                           # Esc: back to the choices, quietly
    path = normalise_pasted_path(typed)
    if path is None:
        return "Nothing was pasted - the folder is unchanged."
    return _set_output(cfg, path)


def _set_output(cfg, path):
    """Use this folder. None on success, otherwise why it cannot be used."""
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        log(f"That folder cannot be used ({e}) - nothing changed.", "ERROR")
        return f"That folder cannot be used: {e}"
    cfg["output_dir"] = str(path)
    save_config(cfg)
    log(f"Downloads will be saved to: {path}", "OK")
    return None


def _welcome(cfg):
    """True to start, False when setup was skipped, None for Esc."""
    choice = run_panel(
        "Welcome to MediaGrabber",
        "Three quick questions, then you are set up: 1. where to save "
        "downloads, 2. which browser holds your login, 3. tools. You can "
        "change any of these later from the menu.",
        [], {},
        [("start", "Start setup", "about a minute", "1"),
         ("skip", "Skip setup", "keep the defaults", "0")])
    if choice == "cancel":
        return None
    if choice == "skip":
        cfg["setup_done"] = True
        save_config(cfg)
        return False
    return True


def run_setup(cfg, force=False):
    """Walk a new user through the three decisions that matter.

    Skipped without a real terminal: a piped or redirected run has nobody to
    answer, and hanging on a prompt there would break every scripted use.
    Returns a one-line summary for the menu, or None if nothing was set up.
    """
    if not is_interactive():
        if force:
            log("Setup needs a real terminal window.", "WARN")
        return None

    # Each step answers None for Esc, which steps back one question; Esc on
    # the first one leaves the wizard without marking it done.
    steps = (_welcome,
             lambda c: ask_output_folder(c, first_run=True),
             lambda c: choose_browser(c, first_run=True))
    i = 0
    while i < len(steps):
        answer = steps[i](cfg)
        if answer is False:
            return "Setup skipped - defaults kept."
        if answer is None:
            if i == 0:
                return None
            i -= 1
        else:
            i += 1

    # The only case where a first-timer must install something themselves.
    if needs_system_python():
        offer_python_install(ask=True)

    cfg["setup_done"] = True
    save_config(cfg)

    summary = (f"Downloads go to: {cfg.get('output_dir', OUTPUT_DIR)}.  "
               f"Login browser: {cfg.get('cookies_browser', 'auto')}.  "
               "Paste a link with [1] Download Single URL, or fill your list "
               "with [3] Edit Batch URLs and run [2] Download Batch.")

    if confirm("Setup complete — open your downloads folder now?", summary):
        try:
            open_path(cfg.get("output_dir", OUTPUT_DIR))
        except Exception:
            pass
    return "Setup complete."
