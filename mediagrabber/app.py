"""Menu loop and top-level entry point."""

import traceback
from pathlib import Path

from . import APP_VERSION
from .checkup import run_checkup
from .config import (AUDIO_FORMATS, CONFIG_FILE, COOKIES_FILE, LOGS_DIR,
                     OUTPUT_DIR, RESOLUTION_OPTIONS, URLS_FILE, VIDEO_FORMATS,
                     load_config, save_config)
from .cookies import choose_cookie_browser, detect_installed_browsers
from .download import DownloadStopped, download_single
from .nativehost import (CHROME_EXTENSION_ID, bridge_binary, register,
                         status, unregister)
from .platform_support import (IS_WIN, OS_LABEL, PLATFORM_TAG, enable_ansi,
                               open_path, pick_folder_dialog,
                               set_console_title, stop_hint_text)
from .tools import run_updates
from .ui import (C, MARK_OFF, MARK_ON, banner, init_logging, log, log_file,
                 rule)

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


# ── SETTINGS SCREENS ─────────────────────────────────────────────────────────

def _short_label(key, value):
    """Compact name for the settings grid — the long blurb goes underneath."""
    if key == "resolution":
        return {"best": "Best", "worst": "Worst"}.get(value, f"{value}p")
    if key == "mode":
        return {"video": "Video / Image", "audio": "Audio"}.get(value, value)
    return value.upper()


def format_status(cfg):
    if cfg.get("mode", "video") == "audio":
        return f"Audio ({cfg.get('audio_format', 'mp3').upper()})"
    res = _short_label("resolution", cfg.get("resolution", "best"))
    return f"Video / Image ({cfg.get('video_format', 'mp4').upper()} @ {res})"


def show_menu(cfg):
    print(f"""
{C.BOLD}┌───────────────────────────────────────────────┐
│  {C.CYAN}FORMAT{C.RESET}{C.BOLD}:      {C.WHITE}{format_status(cfg)}{C.RESET}{C.BOLD}
│  {C.CYAN}AUTO-UPDATE{C.RESET}{C.BOLD}: {C.WHITE}{"ON" if cfg.get("auto_update", True) else "OFF"}{C.RESET}{C.BOLD}
│  {C.CYAN}OUTPUT{C.RESET}{C.BOLD}:      {C.DIM}{cfg.get("output_dir", OUTPUT_DIR)}{C.RESET}{C.BOLD}
│  {C.CYAN}PLATFORM{C.RESET}{C.BOLD}:    {C.DIM}{OS_LABEL} ({PLATFORM_TAG}){C.RESET}{C.BOLD}
├───────────────────────────────────────────────┤
│  {C.GREEN}[1]{C.RESET}{C.BOLD}  Download Settings                       │
│  {C.GREEN}[2]{C.RESET}{C.BOLD}  Download Batch      {C.DIM}(from urls.txt){C.RESET}{C.BOLD}     │
│  {C.GREEN}[3]{C.RESET}{C.BOLD}  Download Single URL                     │
│  {C.GREEN}[4]{C.RESET}{C.BOLD}  Login & Browser                         │
│  {C.GREEN}[5]{C.RESET}{C.BOLD}  Tools Update                            │
│  {C.GREEN}[6]{C.RESET}{C.BOLD}  Open Output Folder                      │
│  {C.GREEN}[0]{C.RESET}{C.BOLD}  Exit                                    │
└───────────────────────────────────────────────┘{C.RESET}
""")


def _settings_rows(cfg):
    """(heading, config key, [(value, label, blurb)]) for the current mode.

    The mode row decides what the rows below it contain: audio has its own
    format list and no resolution at all, so switching mode reshapes the screen
    rather than leaving dead options on it.
    """
    rows = [("MODE", "mode",
             [(v, _short_label("mode", v), blurb) for v, blurb in
              (("video", "Video files, and the images in a post"),
               ("audio", "Extract the audio track only"))])]

    if cfg.get("mode", "video") == "audio":
        rows.append(("FILE FORMAT", "audio_format",
                     [(v, _short_label("audio_format", v), d)
                      for v, d in AUDIO_FORMATS]))
    else:
        rows.append(("FILE FORMAT", "video_format",
                     [(v, _short_label("video_format", v), d)
                      for v, d in VIDEO_FORMATS]))
        rows.append(("RESOLUTION", "resolution",
                     [(v, _short_label("resolution", v), d)
                      for v, d in RESOLUTION_OPTIONS]))
    return rows


def _draw_settings(cfg):
    """Render every setting on one screen. Returns {typed number: (key, value)}."""
    print(f"\n  {C.BOLD}{C.CYAN}Download Settings{C.RESET}"
          f"   {C.DIM}pick one per row{C.RESET}")
    print(f"  {C.DIM}{'─' * 60}{C.RESET}")

    index = {}
    n = 0
    for heading, key, options in _settings_rows(cfg):
        current = cfg.get(key)
        chosen = next((b for v, _, b in options if v == current), "")
        print(f"\n  {C.CYAN}{heading}{C.RESET}  {C.DIM}{C.ITALIC}{chosen}{C.RESET}")

        # Cells carry colour codes, so pad on the plain text or the columns
        # drift apart as soon as anything is selected.
        width = max(len(label) for _, label, _ in options) + 9
        per_line = max(1, 68 // width)
        line, in_line = "", 0
        for value, label, _ in options:
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

    auto = "ON" if cfg.get("auto_update", True) else "OFF"
    print(f"\n  {C.DIM}{'─' * 60}{C.RESET}")
    print(f"  {C.GREEN}[o]{C.RESET} Output folder   "
          f"{C.GREEN}[u]{C.RESET} Auto-update: {C.WHITE}{auto}{C.RESET}   "
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
            cfg[key] = value
            save_config(cfg)
            continue

        if choice == "o":
            change_output_folder(cfg)
        elif choice == "u":
            cfg["auto_update"] = not cfg.get("auto_update", True)
            save_config(cfg)
            log(f"Auto-update {'enabled' if cfg['auto_update'] else 'disabled'}", "OK")
        elif choice == "e":
            open_path(URLS_FILE)
        else:
            log("Not one of the options.", "WARN")


def change_output_folder(cfg):
    current = cfg.get("output_dir", OUTPUT_DIR)
    print(f"\n  {C.BOLD}{C.CYAN}Change Output Folder{C.RESET}")
    print(f"  {C.DIM}Current: {current}{C.RESET}\n")
    print(f"  {C.GREEN}[1]{C.RESET} Type a new path")
    print(f"  {C.GREEN}[2]{C.RESET} Browse with folder picker")
    print(f"  {C.GREEN}[3]{C.RESET} Reset to default")
    print(f"  {C.DIM}[0] Cancel{C.RESET}\n")
    choice = input(f"  {C.CYAN}#{C.RESET} ").strip()

    if choice == "1":
        new_path = input(f"  {C.CYAN}New path:{C.RESET} ").strip().strip('"').strip("'")
        if not new_path:
            log("No path entered.", "WARN")
            return
        # A dragged-in folder on macOS/Linux arrives with escaped spaces.
        if not IS_WIN:
            new_path = new_path.replace("\\ ", " ")
        p = Path(new_path).expanduser()
        try:
            p.mkdir(parents=True, exist_ok=True)
            cfg["output_dir"] = str(p)
            save_config(cfg)
            log(f"Output folder changed to: {p}", "OK")
        except Exception as e:
            log(f"Invalid path: {e}", "ERROR")

    elif choice == "2":
        picked = pick_folder_dialog(current)
        if picked:
            cfg["output_dir"] = picked
            save_config(cfg)
            log(f"Output folder changed to: {picked}", "OK")
        else:
            log("No folder selected (or no native picker here) — use option [1].", "WARN")

    elif choice == "3":
        cfg["output_dir"] = OUTPUT_DIR
        save_config(cfg)
        log(f"Output folder reset to default: {OUTPUT_DIR}", "OK")


def prompt_resolution(url):
    print(f"\n  {C.BOLD}{C.CYAN}Select Resolution for this download:{C.RESET}")
    print(f"  {C.DIM}URL: {url[:80]}{'...' if len(url) > 80 else ''}{C.RESET}")
    print(f"  {C.DIM}{'─' * 45}{C.RESET}")
    for i, (_, label) in enumerate(RESOLUTION_OPTIONS, 1):
        print(f"  {C.GREEN}[{i:>2}]{C.RESET} {label}")
    print(f"  {C.DIM}[ 0] Use default (best){C.RESET}\n")
    try:
        choice = input(f"  {C.CYAN}#{C.RESET} ").strip()
        if not choice or choice == "0":
            return "best"
        idx = int(choice) - 1
        if 0 <= idx < len(RESOLUTION_OPTIONS):
            log(f"Resolution: {RESOLUTION_OPTIONS[idx][1]}", "OK")
            return RESOLUTION_OPTIONS[idx][0]
    except (ValueError, EOFError):
        pass
    return "best"


# ── BATCH / SINGLE ───────────────────────────────────────────────────────────

def process_batch(cfg):
    urls = read_urls()
    if not urls:
        log("No URLs found in urls.txt — add some and try again.", "WARN")
        log(f"File location: {URLS_FILE}", "INFO")
        return

    total = len(urls)
    log(f"Found {total} URL(s) to process", "INFO")

    batch_res = None
    if cfg.get("mode", "video") == "video":
        print(f"\n  {C.BOLD}Pick resolution for this batch (or Enter for default):{C.RESET}")
        for i, (val, label) in enumerate(RESOLUTION_OPTIONS, 1):
            marker = f" {C.GREEN}<- default{C.RESET}" if val == cfg.get("resolution", "best") else ""
            print(f"  {C.GREEN}[{i:>2}]{C.RESET} {label}{marker}")
        print(f"  {C.DIM}[Enter] Use default{C.RESET}\n")
        try:
            rc = input(f"  {C.CYAN}#{C.RESET} ").strip()
            if rc:
                idx = int(rc) - 1
                if 0 <= idx < len(RESOLUTION_OPTIONS):
                    batch_res = RESOLUTION_OPTIONS[idx][0]
                    log(f"Batch resolution: {RESOLUTION_OPTIONS[idx][1]}", "OK")
        except (ValueError, EOFError):
            pass

    rule()
    log(stop_hint_text(), "INFO")

    results = []
    stopped = False
    for i, url in enumerate(urls, 1):
        try:
            results.append(download_single(url, cfg, i, total, resolution_override=batch_res))
        except DownloadStopped:
            log("Downloads stopped by user — remaining URLs kept in urls.txt.", "WARN")
            stopped = True
            break
        rule()

    ok_count = sum(1 for _, ok, _ in results if ok)
    fail_count = total - ok_count

    print(f"\n{C.BOLD}{'═' * 55}")
    print(f"  SUMMARY: {C.GREEN}{ok_count} succeeded{C.RESET}{C.BOLD}, "
          f"{C.RED}{fail_count} failed{C.RESET}{C.BOLD} / {total} total")
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

    res_override = prompt_resolution(url) if cfg.get("mode", "video") == "video" else None

    rule()
    log(stop_hint_text(), "INFO")
    try:
        download_single(url, cfg, 1, 1, resolution_override=res_override)
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
            choose_cookie_browser(cfg)
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


def main():
    enable_ansi()
    set_console_title("MediaGrabber")
    init_logging(LOGS_DIR)
    banner()
    log(f"App directory: {CONFIG_FILE.parent}", "INFO")
    log(f"Log file: {log_file()}", "INFO")

    cfg = load_config()
    Path(cfg.get("output_dir", OUTPUT_DIR)).mkdir(parents=True, exist_ok=True)
    save_config(cfg)

    if not URLS_FILE.exists():
        read_urls()
        log(f"Created urls.txt at {URLS_FILE}", "INFO")

    log("Checking tools...", "HEADER")
    if not run_updates(cfg):
        log("Some tools could not be downloaded. Downloads may fail.", "ERROR")
    print()

    run_checkup(cfg, quick=True)
    print()

    actions = {
        "1": lambda: download_settings(cfg),
        "2": lambda: process_batch(cfg),
        "3": lambda: process_single(cfg),
        "4": lambda: login_and_browser(cfg),
        "5": lambda: (log("Forcing tool update...", "UPDATE"),
                      run_updates(cfg, force=True)),
        "6": lambda: open_path(cfg.get("output_dir", OUTPUT_DIR)),
    }

    while True:
        try:
            show_menu(cfg)
            choice = input(f"  {C.CYAN}>{C.RESET} ").strip()

            if choice in actions:
                actions[choice]()
            elif choice == "0":
                log("Exiting. Goodbye!", "INFO")
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

    print()


__all__ = ["main", "APP_VERSION"]
