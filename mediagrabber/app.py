"""Menu loop and top-level entry point."""

import traceback
from pathlib import Path

from . import APP_VERSION
from .checkup import run_checkup
from .config import (CONFIG_FILE, COOKIES_FILE, LOGS_DIR, OUTPUT_DIR,
                     URLS_FILE, load_config, save_config)
from .cookies import detect_installed_browsers
from .download import DownloadStopped, download_single
from .menu import ACTIONS, build_layout, format_status, settings_rows
from .panel import handle_event
from .nativehost import (CHROME_EXTENSION_ID, bridge_binary, register,
                         status, unregister)
from .firstrun import choose_browser, needs_setup, run_setup
from .platform_support import (copy_path_hint, enable_ansi,
                               normalise_pasted_path, open_path,
                               pick_folder_dialog, set_console_title,
                               stop_hint_text)
from .screen import Screen, is_interactive
from .tools import run_updates
from .ui import (C, MARK_OFF, MARK_ON, banner, init_logging, log, log_file,
                 quiet_output, rule)

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
            change_output_folder(cfg)
        elif choice == "e":
            edit_urls()
        else:
            log("Not one of the options.", "WARN")


def change_output_folder(cfg):
    """Pick where downloads land.

    Pasting is listed first because it is what people actually do: nobody
    types a path like "D:/3. Dev Kitchen/yt-dlp" by hand correctly, and the
    file manager will hand them the whole thing on the clipboard.
    """
    current = cfg.get("output_dir", OUTPUT_DIR)
    print()
    print(f"  {C.BOLD}{C.CYAN}Change Output Folder{C.RESET}")
    print(f"  {C.DIM}Current: {current}{C.RESET}")
    print()
    print(f"  {C.GREEN}[1]{C.RESET} Paste a folder path  "
          f"{C.DIM}(copied from your file manager){C.RESET}")
    print(f"  {C.GREEN}[2]{C.RESET} Browse with a folder picker")
    print(f"  {C.GREEN}[3]{C.RESET} Reset to the default folder")
    print(f"  {C.DIM}[0] Cancel{C.RESET}")
    print()
    try:
        choice = input(f"  {C.CYAN}#{C.RESET} ").strip()
    except EOFError:
        return

    if choice == "1":
        print()
        print(f"  {C.BOLD}How to copy a folder's path{C.RESET}")
        print(f"  {C.DIM}{copy_path_hint()}{C.RESET}")
        print(f"  {C.DIM}Quotes are fine — paste exactly what you copied.{C.RESET}")
        print()
        try:
            typed = input(f"  {C.CYAN}Paste the folder path:{C.RESET} ")
        except EOFError:
            return
        path = normalise_pasted_path(typed)
        if path is None:
            log("Nothing was pasted — the output folder is unchanged.", "WARN")
            return
        try:
            path.mkdir(parents=True, exist_ok=True)
            cfg["output_dir"] = str(path)
            save_config(cfg)
            log(f"Downloads will be saved to: {path}", "OK")
        except Exception as e:
            log(f"That folder cannot be used: {e}", "ERROR")

    elif choice == "2":
        picked = pick_folder_dialog(current)
        if picked:
            cfg["output_dir"] = picked
            save_config(cfg)
            log(f"Downloads will be saved to: {picked}", "OK")
        else:
            log("No folder selected (or no picker on this system) — "
                "use option [1] and paste the path instead.", "WARN")

    elif choice == "3":
        cfg["output_dir"] = OUTPUT_DIR
        save_config(cfg)
        log(f"Output folder reset to default: {OUTPUT_DIR}", "OK")


# ── BATCH / SINGLE ───────────────────────────────────────────────────────────

def process_batch(cfg):
    urls = read_urls()
    if not urls:
        log("No URLs found in urls.txt — add some and try again.", "WARN")
        log(f"File location: {URLS_FILE}", "INFO")
        return

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


def process_single(cfg):
    print()
    url = input(f"  {C.CYAN}Paste URL:{C.RESET} ").strip()
    if not url:
        log("No URL entered.", "WARN")
        return

    log(f"Format: {format_status(cfg)}", "INFO")
    rule()
    log(stop_hint_text(), "INFO")
    try:
        download_single(url, cfg, 1, 1)
    except DownloadStopped:
        log("Download stopped by user.", "WARN")
    rule()


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
        return

    binary = bridge_binary()
    print(f"\n  {C.BOLD}{C.CYAN}Browser extension bridge{C.RESET}")
    print(f"  {C.DIM}{'-' * 45}{C.RESET}")
    print(f"  Bridge: {C.DIM}{binary}{C.RESET}")
    if not binary.exists():
        log("Bridge program not found next to the app — rebuild with BUILD.bat "
            "or run from source.", "WARN")

    for browser, ok, detail in status(browsers):
        mark = f"{C.GREEN}connected{C.RESET}" if ok else f"{C.DIM}not connected{C.RESET}"
        print(f"  {browser:<10} {mark}  {C.DIM}{detail}{C.RESET}")

    print(f"\n  {C.GREEN}[1]{C.RESET} Connect these browsers")
    print(f"  {C.GREEN}[2]{C.RESET} Disconnect")
    print(f"  {C.DIM}[0] Cancel{C.RESET}\n")
    choice = input(f"  {C.CYAN}#{C.RESET} ").strip()

    if choice == "1":
        for browser, ok, detail in register(browsers):
            log(f"  {browser}: {'registered' if ok else 'failed'} — {detail}",
                "OK" if ok else "ERROR")
        log("Now load the extension in your browser:", "INFO")
        log(f"  Chromium: chrome://extensions -> Developer mode -> "
            f"Load unpacked -> the 'extension' folder (ID {CHROME_EXTENSION_ID})", "INFO")
        log("  Firefox:  install the signed .xpi from the MediaGrabber release", "INFO")
        log("Then click the extension and choose 'Send my login to MediaGrabber'.",
            "INFO")
    elif choice == "2":
        for browser, ok, detail in unregister(browsers):
            log(f"  {browser}: {detail}", "OK" if ok else "INFO")


def login_and_browser(cfg):
    """Everything about proving who you are to a site, in one place."""
    while True:
        source = "cached file" if COOKIES_FILE.exists() else "none saved"
        print(f"\n  {C.BOLD}{C.CYAN}Login & Browser{C.RESET}")
        print(f"  {C.DIM}{'─' * 45}{C.RESET}")
        print(f"  Cookie browser: {C.WHITE}{cfg.get('cookies_browser', 'auto')}{C.RESET}"
              f"   Saved login: {C.WHITE}{source}{C.RESET}\n")
        print(f"  {C.GREEN}[1]{C.RESET} Set login cookie browser")
        print(f"  {C.GREEN}[2]{C.RESET} Connect browser extension  "
              f"{C.DIM}(hands your login over directly){C.RESET}")
        print(f"  {C.GREEN}[3]{C.RESET} Checkup  {C.DIM}(tools + login){C.RESET}")
        print(f"  {C.GREEN}[4]{C.RESET} Delete saved login cookies")
        print(f"  {C.DIM}[0] Back{C.RESET}\n")
        try:
            choice = input(f"  {C.CYAN}#{C.RESET} ").strip()
        except EOFError:
            return

        if choice in ("0", ""):
            return
        if choice == "1":
            choose_browser(cfg)
        elif choice == "2":
            connect_extension(cfg)
        elif choice == "3":
            run_checkup(cfg)
        elif choice == "4":
            if COOKIES_FILE.exists():
                try:
                    COOKIES_FILE.unlink()
                    log("Saved login cookies deleted. They will be re-exported "
                        "from your browser when next needed.", "OK")
                except Exception as e:
                    log(f"Could not delete cookie cache: {e}", "ERROR")
            else:
                log("No saved cookies to delete.", "INFO")
        else:
            log("Not one of the options.", "WARN")


def run_action(name, cfg):
    """Perform one menu action. Returns True when the app should exit."""
    if name == "single":
        process_single(cfg)
    elif name == "batch":
        process_batch(cfg)
    elif name == "edit":
        edit_urls()
    elif name == "login":
        login_and_browser(cfg)
    elif name == "update":
        log("Forcing tool update...", "UPDATE")
        run_updates(cfg, force=True)
    elif name == "output":
        open_path(cfg.get("output_dir", OUTPUT_DIR))
    elif name == "folder":
        change_output_folder(cfg)
    elif name == "setup":
        run_setup(cfg, force=True)
    elif name == "exit":
        log("Exiting. Goodbye!", "INFO")
        return True
    return False


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
                        escape_action="exit")


def _choose_interactively(cfg, focus, problem=None):
    """Own the terminal until the user picks an action, then give it back.

    Raw mode is left before anything runs, because a download scrolls its own
    output and its Q-to-stop check reads the terminal the ordinary way.
    """
    with Screen() as scr:
        while True:
            layout = build_layout(cfg, focus["row"], focus["col"],
                                  login=login_source(), problem=problem)
            scr.render(layout.lines)
            event = scr.read_event()
            if event is None:
                continue
            action = _handle_event(cfg, layout, focus, event)
            if action:
                return action


def _interactive_loop(cfg, problem=None):
    focus = {"row": 0, "col": 0}
    while True:
        try:
            action = _choose_interactively(cfg, focus, problem)
        except KeyboardInterrupt:
            print()
            log("Interrupted by user. Exiting.", "WARN")
            return

        print()
        try:
            if run_action(action, cfg):
                return
        except KeyboardInterrupt:
            print()
            log("Interrupted.", "WARN")
        except Exception as e:
            log(f"Unexpected error: {e}", "ERROR")
            log(traceback.format_exc(), "ERROR")

        try:
            input(f"\n  {C.DIM}Press Enter to return to the menu...{C.RESET}")
        except (EOFError, KeyboardInterrupt):
            return


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
                if run_action(by_number[choice], cfg):
                    break
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


def main():
    enable_ansi()
    set_console_title("MediaGrabber")
    init_logging(LOGS_DIR)
    banner()

    cfg = load_config()
    Path(cfg.get("output_dir", OUTPUT_DIR)).mkdir(parents=True, exist_ok=True)
    save_config(cfg)

    if not URLS_FILE.exists():
        read_urls()

    # Starting up has a lot to say and almost none of it is news. All of it
    # still reaches the session log; the screen only hears about it when
    # something is actually wrong.
    print(f"  {C.DIM}Checking tools...{C.RESET}", end="", flush=True)
    with quiet_output():
        log(f"App directory: {CONFIG_FILE.parent}", "INFO")
        log(f"Log file: {log_file()}", "INFO")
        # Never interrupts startup with a question; a problem is
        # surfaced on the menu instead, where it stays visible.
        tools_ok = run_updates(cfg, interactive=False)
        healthy = run_checkup(cfg, quick=True) and tools_ok
    print("\r" + " " * 40 + "\r", end="")

    # Carried into the menu rather than printed here: the first repaint starts
    # at the top of the window and would paint over anything printed first.
    problem = None
    if not healthy:
        problem = f"Startup found problems — see {log_file()}"

    if needs_setup(cfg) and is_interactive():
        run_setup(cfg)

    if is_interactive():
        _interactive_loop(cfg, problem)
    else:
        if problem:
            log(problem, "WARN")
        _typed_loop(cfg)

    print()


__all__ = ["main", "APP_VERSION"]
