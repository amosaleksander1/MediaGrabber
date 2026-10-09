"""Menu loop and top-level entry point."""

import sys
import traceback
from pathlib import Path

from . import APP_VERSION
from .checkup import run_checkup
from .config import (CONFIG_FILE, COOKIES_FILE, LOGS_DIR, OUTPUT_DIR,
                     URLS_FILE, load_config, save_config)
from .cookies import detect_installed_browsers
from .download import DownloadStopped, download_single
from .menu import ACTIONS, build_layout, format_status, settings_rows
from .panel import confirm, handle_event, pause, run_panel
from .nativehost import (CHROME_EXTENSION_ID, bridge_binary, register,
                         status, unregister)
from .firstrun import ask_output_folder, choose_browser, needs_setup, run_setup
from .platform_support import (enable_ansi, open_path, set_console_title,
                               stop_hint_text)
from .screen import Screen, is_interactive, read_line
from .tools import run_updates, tools_present
from .ui import (C, MARK_OFF, MARK_ON, banner, init_logging, log, log_file,
                 quiet_output, rule)

#: What an action returns when the app should close. Everything else an action
#: returns is a one-line notice for the menu, or None for nothing to say.
EXIT = object()

# ── URL FILE ─────────────────────────────────────────────────────────────────

_URLS_HEADER = ("# MediaGrabber URL List\n"
                "# Paste one URL per line. Lines starting with # are ignored.\n")


def read_urls():
    if not URLS_FILE.exists():
        URLS_FILE.write_text(_URLS_HEADER + "# Save this file, then run MediaGrabber.\n\n",
                             encoding="utf-8")
        return []
    urls = []
    with open(URLS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
    return urls


def clear_urls():
    URLS_FILE.write_text(_URLS_HEADER + "# All URLs processed — paste new ones here.\n\n",
                         encoding="utf-8")


def edit_urls():
    """Open urls.txt in whatever the OS uses for text files."""
    if not URLS_FILE.exists():
        read_urls()
    log(f"Opening {URLS_FILE}", "INFO")
    try:
        open_path(URLS_FILE)
    except Exception as e:
        log(f"Could not open the file ({e}). Edit it yourself at: {URLS_FILE}",
            "WARN")


def edit_batch(cfg):
    """Open the list, then wait: the user is off in another window typing.

    Going straight back to the menu would leave them to remember that [2]
    is the next step; offering it here is the one place a pause earns itself.
    """
    edit_urls()
    if not is_interactive():
        return None
    choice = run_panel(
        "Add your links to the batch list",
        f"{URLS_FILE.name} is open in your text editor. Paste one link per "
        "line, save the file, then choose:",
        [], {},
        [("batch", "Download batch now", "every link in the list", "1"),
         ("back", "Back to menu", "", "0")])
    if choice == "batch":
        return run_action("batch", cfg)
    return None


# ── SETTINGS SCREENS ─────────────────────────────────────────────────────────

def login_source():
    """Short description of where a login would come from, for the header."""
    return "saved" if COOKIES_FILE.exists() else "none saved"


def apply_setting(cfg, key, value):
    cfg[key] = value
    save_config(cfg)


def show_menu(cfg):
    """Print the menu once, for terminals that cannot be driven interactively."""
    layout = build_layout(cfg, login=login_source(), interactive=False,
                          with_banner=False)
    print()
    for line in layout.lines:
        print(line)
    print(f"  {C.DIM}Type a number, or S to change the download settings.{C.RESET}\n")


def _draw_settings(cfg):
    """Typed settings screen — the fallback when there is no usable terminal.

    The interactive menu edits these values in place; this exists so a piped or
    redirected session can still reach them.
    """
    print(f"\n  {C.BOLD}{C.CYAN}Download Settings{C.RESET}"
          f"   {C.DIM}pick one per row{C.RESET}")
    print(f"  {C.DIM}{'─' * 60}{C.RESET}")

    index = {}
    n = 0
    for heading, key, options in settings_rows(cfg):
        current = cfg.get(key)
        print(f"\n  {C.CYAN}{heading}{C.RESET}")

        # Cells carry colour codes, so pad on the plain text or the columns
        # drift apart as soon as anything is selected.
        width = max(len(label) for _, label in options) + 9
        per_line = max(1, 68 // width)
        line, in_line = "", 0
        for value, label in options:
            n += 1
            index[str(n)] = (key, value)
            mark = (f"{C.GREEN}{MARK_ON}{C.RESET}" if value == current
                    else f"{C.DIM}{MARK_OFF}{C.RESET}")
            plain = f"[{n:>2}] {MARK_ON} {label}"
            cell = f"{C.GREEN}[{n:>2}]{C.RESET} {mark} {label}"
            line += cell + " " * max(1, width - len(plain))
            in_line += 1
            if in_line == per_line:
                print("    " + line.rstrip())
                line, in_line = "", 0
        if line.strip():
            print("    " + line.rstrip())

    print(f"\n  {C.DIM}{'─' * 60}{C.RESET}")
    print(f"  {C.GREEN}[o]{C.RESET} Output folder   "
          f"{C.GREEN}[e]{C.RESET} Edit urls.txt   {C.DIM}[0] Back{C.RESET}\n")
    return index


def download_settings(cfg):
    """Mode, format and resolution on one screen, redrawn after every change."""
    while True:
        index = _draw_settings(cfg)
        try:
            choice = input(f"  {C.CYAN}#{C.RESET} ").strip().lower()
        except EOFError:
            return

        if choice in ("0", "", "b", "back"):
            return

        if choice in index:
            key, value = index[choice]
            apply_setting(cfg, key, value)
            continue

        if choice == "o":
            ask_output_folder(cfg)
        elif choice == "e":
            edit_urls()
        else:
            log("Not one of the options.", "WARN")


# ── BATCH / SINGLE ───────────────────────────────────────────────────────────

def process_batch(cfg):
    """Download every link in urls.txt. Returns [(url, ok, message), ...]."""
    urls = read_urls()
    if not urls:
        log("No URLs found in urls.txt — add some and try again.", "WARN")
        log(f"File location: {URLS_FILE}", "INFO")
        return []

    total = len(urls)
    log(f"Found {total} URL(s) to process", "INFO")
    log(f"Format: {format_status(cfg)}", "INFO")

    rule()
    log(stop_hint_text(), "INFO")

    results = []
    stopped = False
    for i, url in enumerate(urls, 1):
        try:
            results.append(download_single(url, cfg, i, total))
        except DownloadStopped:
            log("Downloads stopped by user — remaining URLs kept in urls.txt.", "WARN")
            stopped = True
            break
        rule()

    # Count against what was actually attempted: stopping half way through
    # leaves the rest untouched in urls.txt, and calling those "failed" both
    # reads as breakage and hides the real failures in the list below.
    attempted = len(results)
    ok_count = sum(1 for _, ok, _ in results if ok)
    fail_count = attempted - ok_count

    print(f"\n{C.BOLD}{'═' * 55}")
    print(f"  SUMMARY: {C.GREEN}{ok_count} succeeded{C.RESET}{C.BOLD}, "
          f"{C.RED}{fail_count} failed{C.RESET}{C.BOLD} / {attempted} attempted"
          + (f" ({total - attempted} not started)" if attempted < total else ""))
    print(f"{'═' * 55}{C.RESET}\n")

    if fail_count > 0:
        log("Failed URLs:", "ERROR")
        for url, ok, msg in results:
            if not ok:
                log(f"  {url} — {msg}", "ERROR")
    elif not stopped:
        clear_urls()
        log("All downloads succeeded. urls.txt cleared.", "OK")
    return results


def process_single(cfg):
    """Ask for one link and download it. Esc at the prompt goes back."""
    print()
    url = read_line(f"  {C.CYAN}Paste URL{C.RESET} {C.DIM}(Esc to go back):{C.RESET} ")
    if url is None:
        return None
    url = url.strip()
    if not url:
        return "No URL entered."

    log(f"Format: {format_status(cfg)}", "INFO")
    rule()
    log(stop_hint_text(), "INFO")
    try:
        _, ok, msg = download_single(url, cfg, 1, 1)
    except DownloadStopped:
        log("Download stopped by user.", "WARN")
        rule()
        return "Download stopped."
    rule()
    pause()
    return f"Downloaded: {url}" if ok else f"Download failed: {msg}"


# ── MAIN ─────────────────────────────────────────────────────────────────────

def connect_extension(cfg):
    """Register (or remove) the native-messaging host for installed browsers.

    A browser will only launch the bridge if it has been told about it, via a
    manifest naming the executable and the extension IDs allowed to connect.
    Everything written here is per-user: no admin rights, nothing system-wide.
    """
    browsers = detect_installed_browsers()
    if not browsers:
        log("No supported browsers detected.", "WARN")
        return "No supported browsers detected."

    binary = bridge_binary()
    states = ", ".join(f"{b.title()}: {'connected' if ok else 'not connected'}"
                       for b, ok, _ in status(browsers))
    note = f"{states}.   Bridge: {binary}"
    if not binary.exists():
        note += ("   ! Bridge program not found next to the app - rebuild "
                 "with BUILD.bat or run from source.")

    choice = run_panel("Browser extension bridge", note, [], {},
                       [("connect", "Connect these browsers", "", "1"),
                        ("disconnect", "Disconnect", "", "2"),
                        ("back", "Back", "", "0")])

    if choice == "connect":
        print()
        for browser, ok, detail in register(browsers):
            log(f"  {browser}: {'registered' if ok else 'failed'} — {detail}",
                "OK" if ok else "ERROR")
        log("Now load the extension in your browser:", "INFO")
        log(f"  Chromium: chrome://extensions -> Developer mode -> "
            f"Load unpacked -> the 'extension' folder (ID {CHROME_EXTENSION_ID})", "INFO")
        log("  Firefox:  install the signed .xpi from the MediaGrabber release", "INFO")
        log("Then click the extension and choose 'Send my login to MediaGrabber'.",
            "INFO")
        pause("Enter or Esc: back")
        return "Browsers connected - load the extension to finish."
    if choice == "disconnect":
        for browser, ok, detail in unregister(browsers):
            log(f"  {browser}: {detail}", "OK" if ok else "INFO")
        return "Extension bridge disconnected."
    return None


def login_and_browser(cfg):
    """Everything about proving who you are to a site, in one place."""
    msg = ""
    while True:
        source = "cached file" if COOKIES_FILE.exists() else "none saved"
        note = (f"Login browser: {cfg.get('cookies_browser', 'auto')}   "
                f"Saved login: {source}")
        if msg:
            note += f"   > {msg}"
        choice = run_panel(
            "Login & Browser", note, [], {},
            [("browser", "Set login browser", "the one you use for Instagram", "1"),
             ("extension", "Connect browser extension",
              "hands your login over directly", "2"),
             ("checkup", "Checkup", "tools + login", "3"),
             ("delete", "Delete saved login cookies", "", "4"),
             ("back", "Back", "", "0")])

        if choice in ("back", "cancel"):
            return msg or None
        if choice == "browser":
            picked = choose_browser(cfg)
            msg = f"Login will be read from: {picked}" if picked else ""
        elif choice == "extension":
            msg = connect_extension(cfg) or ""
        elif choice == "checkup":
            print()
            ok = run_checkup(cfg)
            pause("Enter or Esc: back")
            msg = "Checkup passed." if ok else "Checkup found problems - see the log."
        elif choice == "delete":
            if COOKIES_FILE.exists():
                try:
                    COOKIES_FILE.unlink()
                    msg = "Saved login deleted - it is re-exported when next needed."
                except Exception as e:
                    msg = f"Could not delete the saved login: {e}"
            else:
                msg = "No saved login to delete."
            log(msg, "INFO")


def run_action(name, cfg):
    """Perform one menu action.

    Returns ``EXIT`` to close the app, otherwise a one-line notice for the menu
    (or None). Actions whose output is worth reading hold the screen
    themselves; the rest come straight back with the notice.
    """
    if name == "single":
        return process_single(cfg)
    if name == "batch":
        results = process_batch(cfg)
        if not results:
            return "Nothing to download - add links with [3] Edit Batch URLs."
        pause()
        ok = sum(1 for _, good, _ in results if good)
        return f"Batch: {ok} succeeded, {len(results) - ok} failed."
    if name == "edit":
        return edit_batch(cfg)
    if name == "login":
        return login_and_browser(cfg)
    if name == "update":
        log("Forcing tool update...", "UPDATE")
        ok = run_updates(cfg, force=True)
        pause()
        return "Tools are up to date." if ok else "Some tools failed to update - see the log."
    if name == "output":
        folder = cfg.get("output_dir", OUTPUT_DIR)
        open_path(folder)
        return f"Opened {folder}"
    if name == "folder":
        return ask_output_folder(cfg)
    if name == "setup":
        return run_setup(cfg, force=True)
    if name == "quit":
        # Esc means "back" everywhere else, so pressing it once too often must
        # not close the app outright. [0] still exits without asking.
        if not confirm("Exit MediaGrabber?"):
            return None
        name = "exit"
    if name == "exit":
        log("Exiting. Goodbye!", "INFO")
        return EXIT
    return None


def _handle_event(cfg, layout, focus, event):
    """The main menu's keys and clicks — the shared handler, wired to config.

    Every screen in the app runs through panel.handle_event, so an arrow, an
    Enter and a click mean the same thing on the menu, the browser picker and
    the first-run wizard alike.
    """
    return handle_event(layout, focus, event,
                        get_current=cfg.get,
                        set_value=lambda k, v: apply_setting(cfg, k, v),
                        number_actions=[(n, a) for n, a, _, _ in ACTIONS],
                        escape_action="quit")


def _choose_interactively(cfg, focus, problem=None, notice=None):
    """Own the terminal until the user picks an action, then give it back.

    Raw mode is left before anything runs, because a download scrolls its own
    output and its Q-to-stop check reads the terminal the ordinary way.
    """
    with Screen() as scr:
        while True:
            layout = build_layout(cfg, focus["row"], focus["col"],
                                  login=login_source(), problem=problem,
                                  notice=notice)
            scr.render(layout.lines)
            event = scr.read_event()
            if event is None:
                continue
            action = _handle_event(cfg, layout, focus, event)
            if action:
                return action


def _interactive_loop(cfg, problem=None):
    focus = {"row": 0, "col": 0}
    notice = None
    while True:
        try:
            action = _choose_interactively(cfg, focus, problem, notice)
        except KeyboardInterrupt:
            print()
            log("Interrupted by user. Exiting.", "WARN")
            return

        print()
        notice = None
        try:
            result = run_action(action, cfg)
            if result is EXIT:
                return
            notice = result
        except KeyboardInterrupt:
            print()
            log("Interrupted.", "WARN")
            notice = "Interrupted."
        except Exception as e:
            log(f"Unexpected error: {e}", "ERROR")
            log(traceback.format_exc(), "ERROR")
            pause()
            notice = f"Something went wrong: {e}"


def _typed_loop(cfg):
    """The menu as it was: type a number, press Enter.

    Used whenever stdin or stdout is not a terminal — piped input, redirected
    output, CI. Nothing here may depend on raw mode.
    """
    by_number = {number: name for number, name, _, _ in ACTIONS}

    while True:
        try:
            show_menu(cfg)
            choice = input(f"  {C.CYAN}>{C.RESET} ").strip().lower()

            if choice == "s":
                download_settings(cfg)
            elif choice in by_number:
                result = run_action(by_number[choice], cfg)
                if result is EXIT:
                    break
                if result:
                    log(result, "INFO")
            else:
                log("Invalid choice, try again.", "WARN")

        except KeyboardInterrupt:
            print()
            log("Interrupted by user. Exiting.", "WARN")
            break
        except EOFError:
            print()
            log("No input available — exiting.", "WARN")
            break
        except Exception as e:
            log(f"Unexpected error: {e}", "ERROR")
            log(traceback.format_exc(), "ERROR")


def main(argv=None):
    """No arguments: the menu. Any arguments: the command line, for scripts
    and AI agents (``MediaGrabber --help``)."""
    if argv is None:
        # Old macOS hands a Finder-launched app a -psn_ process serial number.
        argv = [a for a in sys.argv[1:] if not a.startswith("-psn_")]
    if argv:
        from .cli import main as cli_main
        sys.exit(cli_main(argv))

    enable_ansi()
    set_console_title("MediaGrabber")
    init_logging(LOGS_DIR)
    banner()

    cfg = load_config()
    Path(cfg.get("output_dir", OUTPUT_DIR)).mkdir(parents=True, exist_ok=True)
    save_config(cfg)

    if not URLS_FILE.exists():
        read_urls()

    # Setup comes before the tools, not after. On a genuinely new install the
    # other order means several minutes of silent downloading, then a cookie
    # export against whatever browser was guessed — which on macOS can raise a
    # Keychain prompt — and only then "welcome, three quick questions". Asking
    # first means the wait is explained and the browser is the user's choice
    # by the time anything tries to read a login from it.
    if needs_setup(cfg) and is_interactive():
        run_setup(cfg)

    # Starting up has a lot to say and almost none of it is news. All of it
    # still reaches the session log; the screen only hears about it when
    # something is actually wrong.
    first_time = not tools_present()
    print(f"  {C.DIM}"
          + ("Getting the downloaders ready — this first run fetches about "
             "250 MB, so give it a minute..." if first_time
             else "Checking tools...")
          + f"{C.RESET}", end="", flush=True)
    with quiet_output():
        log(f"App directory: {CONFIG_FILE.parent}", "INFO")
        log(f"Log file: {log_file()}", "INFO")
        # Never interrupts startup with a question; a problem is
        # surfaced on the menu instead, where it stays visible.
        tools_ok = run_updates(cfg, interactive=False)
        healthy = run_checkup(cfg, quick=True) and tools_ok
    print("\r" + " " * 90 + "\r", end="")

    # Carried into the menu rather than printed here: the first repaint starts
    # at the top of the window and would paint over anything printed first.
    problem = None
    if not healthy:
        problem = f"Startup found problems — see {log_file()}"

    if is_interactive():
        _interactive_loop(cfg, problem)
    else:
        if problem:
            log(problem, "WARN")
        _typed_loop(cfg)

    print()


__all__ = ["main", "APP_VERSION"]
