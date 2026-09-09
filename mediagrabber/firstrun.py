"""The first-run wizard.

Someone opening MediaGrabber for the first time has three things to decide and
no idea that any of them exist: where downloads go, which browser holds the
login it should borrow, and whether the tools it needs are present. Left to
discover those through the menu, the usual first experience is an Instagram
link that fails with an authentication error.

So this asks, once, in the order the answers matter, and every screen says why
it is asking rather than only what it wants.
"""

from pathlib import Path

from .config import OUTPUT_DIR, save_config
from .cookies import browser_order, detect_installed_browsers
from .panel import confirm, pause, run_panel, say
from .platform_support import (copy_path_hint, normalise_pasted_path,
                               open_path, pick_folder_dialog)
from .screen import is_interactive
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
        return choose_cookie_browser(cfg)

    values = {"cookies_browser": cfg.get("cookies_browser", "auto")}
    groups = [("Browser", "cookies_browser", options)]
    actions = [("ok", "Use this browser", ""),
               ("cancel", "Skip for now", "")]

    result = run_panel("Which browser do you use to log into Instagram?",
                       note, groups, values, actions)
    if result != "ok":
        return None

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


def ask_output_folder(cfg):
    """Where downloads land, with the paste trick spelled out.

    Typing a path by hand is the step people get wrong, so the instructions are
    for the thing they will actually do: copy it from the file manager.
    """
    current = cfg.get("output_dir", OUTPUT_DIR)
    values = {"choice": "keep"}
    groups = [("Save to", "choice",
               [("keep", "Keep current"),
                ("paste", "Paste a folder path"),
                ("browse", "Browse..."),
                ("default", "Reset to default")])]
    actions = [("ok", "Continue", ""), ("cancel", "Skip", "")]
    note = f"Downloads currently go to: {current}"

    if is_interactive():
        if run_panel("Where should downloads be saved?", note, groups,
                     values, actions) != "ok":
            return
        choice = values["choice"]
    else:
        choice = "keep"

    if choice == "keep":
        return
    if choice == "default":
        cfg["output_dir"] = OUTPUT_DIR
        save_config(cfg)
        log(f"Output folder reset to: {OUTPUT_DIR}", "OK")
        return
    if choice == "browse":
        picked = pick_folder_dialog(current)
        if picked:
            _set_output(cfg, Path(picked))
        else:
            log("No folder chosen - use 'Paste a folder path' instead.", "WARN")
        return

    say(f"{C.BOLD}How to copy a folder's path{C.RESET}",
        copy_path_hint(),
        "",
        f"{C.DIM}Quotes are fine - paste exactly what you copied.{C.RESET}")
    try:
        typed = input(f"  {C.CYAN}Paste the folder path:{C.RESET} ")
    except (EOFError, KeyboardInterrupt):
        print()
        return
    path = normalise_pasted_path(typed)
    if path is None:
        log("Nothing was pasted - the output folder is unchanged.", "WARN")
        return
    _set_output(cfg, path)


def _set_output(cfg, path):
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        log(f"That folder cannot be used ({e}) - nothing changed.", "ERROR")
        return
    cfg["output_dir"] = str(path)
    save_config(cfg)
    log(f"Downloads will be saved to: {path}", "OK")


def run_setup(cfg, force=False):
    """Walk a new user through the three decisions that matter.

    Skipped without a real terminal: a piped or redirected run has nobody to
    answer, and hanging on a prompt there would break every scripted use.
    """
    if not is_interactive():
        if force:
            log("Setup needs a real terminal window.", "WARN")
        return False

    say(f"{C.BOLD}{C.CYAN}Welcome to MediaGrabber{C.RESET}",
        "",
        "Three quick questions, then you are set up. You can change any of",
        "these later from the menu.",
        "",
        f"{C.DIM}1. Where to save downloads   2. Which browser holds your "
        f"login   3. Tools{C.RESET}")
    pause()

    ask_output_folder(cfg)
    choose_browser(cfg, first_run=True)

    # The only case where a first-timer must install something themselves.
    if needs_system_python():
        say(f"{C.BOLD}One thing is missing{C.RESET}")
        offer_python_install(ask=True)

    cfg["setup_done"] = True
    save_config(cfg)

    # The summary goes *inside* the last panel rather than being printed
    # before it: a panel repaints from the top of the window, so anything
    # printed just beforehand is painted over before it can be read.
    summary = (f"Downloads go to: {cfg.get('output_dir', OUTPUT_DIR)}.  "
               f"Login browser: {cfg.get('cookies_browser', 'auto')}.  "
               "Paste a link with [1] Download Single URL, or fill your list "
               "with [3] Edit Batch URLs and run [2] Download Batch.")

    if confirm("Setup complete — open your downloads folder now?", summary):
        try:
            open_path(cfg.get("output_dir", OUTPUT_DIR))
        except Exception:
            pass

    say(f"{C.GREEN}You are set up.{C.RESET}",
        "",
        f"Downloads go to: {cfg.get('output_dir', OUTPUT_DIR)}",
        f"Login browser:   {cfg.get('cookies_browser', 'auto')}")
    pause("Press Enter to open MediaGrabber...")
    return True
