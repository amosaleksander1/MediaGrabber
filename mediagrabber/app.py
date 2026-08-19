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
                               open_path, pick_folder_dialog, stop_hint_text)
from .tools import run_updates
from .ui import C, banner, init_logging, log, log_file, pick_from_list, rule

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

def format_status(cfg):
    mode = cfg.get("mode", "video")
    if mode == "audio":
        return f"AUDIO ({cfg.get('audio_format', 'mp3').upper()})"
    if mode == "media":
        return "MEDIA (images + video, whole post)"
    res = cfg.get("resolution", "best")
    res_label = res if res in ("best", "worst") else f"{res}p"
    return f"VIDEO ({cfg.get('video_format', 'mp4').upper()} @ {res_label})"


def show_menu(cfg):
    print(f"""
{C.BOLD}┌───────────────────────────────────────────────┐
│  {C.CYAN}FORMAT{C.RESET}{C.BOLD}:      {C.WHITE}{format_status(cfg)}{C.RESET}{C.BOLD}
│  {C.CYAN}AUTO-UPDATE{C.RESET}{C.BOLD}: {C.WHITE}{"ON" if cfg.get("auto_update", True) else "OFF"}{C.RESET}{C.BOLD}
│  {C.CYAN}OUTPUT{C.RESET}{C.BOLD}:      {C.DIM}{cfg.get("output_dir", OUTPUT_DIR)}{C.RESET}{C.BOLD}
│  {C.CYAN}PLATFORM{C.RESET}{C.BOLD}:    {C.DIM}{OS_LABEL} ({PLATFORM_TAG}){C.RESET}{C.BOLD}
├───────────────────────────────────────────────┤
│  {C.GREEN}[1]{C.RESET}{C.BOLD}  Download from urls.txt                  │
│  {C.GREEN}[2]{C.RESET}{C.BOLD}  Download single URL                     │
│  {C.GREEN}[3]{C.RESET}{C.BOLD}  Change format (Video / Audio / Media)   │
│  {C.GREEN}[4]{C.RESET}{C.BOLD}  Change resolution (Video only)          │
│  {C.GREEN}[5]{C.RESET}{C.BOLD}  Change output folder                    │
│  {C.GREEN}[6]{C.RESET}{C.BOLD}  Toggle auto-update                      │
│  {C.GREEN}[7]{C.RESET}{C.BOLD}  Force update tools now                  │
│  {C.GREEN}[8]{C.RESET}{C.BOLD}  Open output folder                      │
│  {C.GREEN}[9]{C.RESET}{C.BOLD}  Open urls.txt for editing               │
│  {C.GREEN}[10]{C.RESET}{C.BOLD} Checkup (tools + login)                 │
│  {C.GREEN}[11]{C.RESET}{C.BOLD} Set login cookie browser                │
│  {C.GREEN}[12]{C.RESET}{C.BOLD} Delete saved login cookies              │
│  {C.GREEN}[13]{C.RESET}{C.BOLD} Connect browser extension               │
│  {C.GREEN}[0]{C.RESET}{C.BOLD}  Exit                                    │
└───────────────────────────────────────────────┘{C.RESET}
""")


def choose_format(cfg):
    print(f"\n  {C.BOLD}{C.CYAN}Choose Mode & Format{C.RESET}")
    print(f"  {C.GREEN}[1]{C.RESET} Video formats")
    print(f"  {C.GREEN}[2]{C.RESET} Audio formats")
    print(f"  {C.GREEN}[3]{C.RESET} Media — every image and video in a post")
    print(f"  {C.DIM}[0] Cancel{C.RESET}\n")
    choice = input(f"  {C.CYAN}#{C.RESET} ").strip()

    if choice == "1":
        picked = pick_from_list("Video Formats", VIDEO_FORMATS, cfg.get("video_format"))
        if picked:
            cfg["mode"], cfg["video_format"] = "video", picked
            save_config(cfg)
            log(f"Format set to VIDEO ({picked.upper()})", "OK")
    elif choice == "2":
        picked = pick_from_list("Audio Formats", AUDIO_FORMATS, cfg.get("audio_format"))
        if picked:
            cfg["mode"], cfg["audio_format"] = "audio", picked
            save_config(cfg)
            log(f"Format set to AUDIO ({picked.upper()})", "OK")
    elif choice == "3":
        cfg["mode"] = "media"
        save_config(cfg)
        log("Format set to MEDIA — pulls every image and video in a post", "OK")
        log("Instagram, TikTok, X/Twitter, Reddit, Pinterest and Threads posts "
            "are named from their caption; other sites are attempted too.", "INFO")


def choose_resolution(cfg):
    if cfg.get("mode") in ("audio", "media"):
        log("Resolution only applies to video mode. Switch to video first.", "WARN")
        return
    picked = pick_from_list("Default Video Resolution", RESOLUTION_OPTIONS,
                            cfg.get("resolution", "best"))
    if picked:
        cfg["resolution"] = picked
        save_config(cfg)
        log(f"Default resolution set to: "
            f"{picked if picked in ('best', 'worst') else picked + 'p'}", "OK")


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


def main():
    enable_ansi()
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
        "1": lambda: process_batch(cfg),
        "2": lambda: process_single(cfg),
        "3": lambda: choose_format(cfg),
        "4": lambda: choose_resolution(cfg),
        "5": lambda: change_output_folder(cfg),
        "7": lambda: (log("Forcing tool update...", "UPDATE"), run_updates(cfg, force=True)),
        "8": lambda: open_path(cfg.get("output_dir", OUTPUT_DIR)),
        "9": lambda: open_path(URLS_FILE),
        "10": lambda: run_checkup(cfg),
        "11": lambda: choose_cookie_browser(cfg),
        "13": lambda: connect_extension(cfg),
    }

    while True:
        try:
            show_menu(cfg)
            choice = input(f"  {C.CYAN}>{C.RESET} ").strip()

            if choice in actions:
                actions[choice]()
            elif choice == "6":
                cfg["auto_update"] = not cfg.get("auto_update", True)
                save_config(cfg)
                log(f"Auto-update {'enabled' if cfg['auto_update'] else 'disabled'}", "OK")
            elif choice == "12":
                if COOKIES_FILE.exists():
                    try:
                        COOKIES_FILE.unlink()
                        log("Saved login cookies deleted. They will be re-exported "
                            "from your browser when next needed.", "OK")
                    except Exception as e:
                        log(f"Could not delete cookie cache: {e}", "ERROR")
                else:
                    log("No saved login cookies to delete.", "INFO")
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
