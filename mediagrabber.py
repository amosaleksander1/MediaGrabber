#!/usr/bin/env python3
"""
MediaGrabber - Portable media downloader with auto-updating tools and live logging.
Reads URLs from urls.txt, downloads via yt-dlp, converts with ffmpeg.
"""

import os
import sys
import subprocess
import zipfile
import tarfile
import shutil
import json
import time
import datetime
import hashlib
import traceback
import re
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

# ── PLATFORM ─────────────────────────────────────────────────────────────────

IS_WIN = sys.platform == "win32"
NO_WINDOW = subprocess.CREATE_NO_WINDOW if IS_WIN else 0
EXE = ".exe" if IS_WIN else ""

def _make_executable(path):
    """chmod +x on Linux/macOS (no-op on Windows)."""
    if not IS_WIN:
        try:
            Path(path).chmod(0o755)
        except Exception:
            pass

def open_path(path):
    """Open a file/folder with the OS default handler."""
    if IS_WIN:
        os.startfile(str(path))
    else:
        subprocess.Popen(["xdg-open", str(path)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# ── GRACEFUL STOP (press Q during downloads) ─────────────────────────────────

class DownloadStopped(Exception):
    """Raised when the user presses Q to stop the current download."""

if IS_WIN:
    import msvcrt

def _stop_requested():
    """Non-blocking check: did the user press Q?
       Windows: instant keypress. Linux: type q then Enter."""
    try:
        if IS_WIN:
            while msvcrt.kbhit():
                if msvcrt.getwch().lower() == "q":
                    return True
        else:
            import select
            r, _, _ = select.select([sys.stdin], [], [], 0)
            if r:
                if sys.stdin.readline().strip().lower() == "q":
                    return True
    except Exception:
        pass
    return False

def _stop_process(process):
    """Terminate a child process, escalating to kill if needed."""
    try:
        process.terminate()
        try:
            process.wait(timeout=5)
        except Exception:
            process.kill()
    except Exception:
        pass

def _stop_hint():
    hint = "Press Q to stop downloads" + ("" if IS_WIN else " (Q then Enter)")
    log(hint, "INFO")

# ── CONFIGURATION ────────────────────────────────────────────────────────────

APP_NAME = "MediaGrabber"
APP_VERSION = "2.2.0"

# Only check for tool updates this often (or when a tool is missing/broken)
UPDATE_INTERVAL_DAYS = 14

# Where downloads land (override via config.json / menu [5])
OUTPUT_DIR = str(Path.home() / "Downloads" / "MediaGrabber")

# App root = folder where the .exe lives
if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).parent
else:
    APP_DIR = Path(__file__).parent

TOOLS_DIR = APP_DIR / "tools"
LOGS_DIR = APP_DIR / "logs"
URLS_FILE = APP_DIR / "urls.txt"
CONFIG_FILE = APP_DIR / "config.json"
VERSION_FILE = TOOLS_DIR / "versions.json"

YTDLP_EXE = TOOLS_DIR / f"yt-dlp{EXE}"
FFMPEG_EXE = TOOLS_DIR / f"ffmpeg{EXE}"
FFPROBE_EXE = TOOLS_DIR / f"ffprobe{EXE}"
DENO_EXE = TOOLS_DIR / f"deno{EXE}"
GALLERYDL_EXE = TOOLS_DIR / f"gallery-dl{EXE}"
COOKIES_FILE = TOOLS_DIR / "cookies.txt"   # cached login cookies (Netscape format)

# Download sources
YTDLP_RELEASE_API = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
YTDLP_DOWNLOAD_URL = (
    "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
    if IS_WIN else
    "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp"
)
FFMPEG_RELEASE_API = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest"
DENO_RELEASE_API = "https://api.github.com/repos/denoland/deno/releases/latest"
# gallery-dl binaries are published in the separate gdl-org/builds repo
GALLERYDL_RELEASE_API = "https://api.github.com/repos/gdl-org/builds/releases/latest"

# Errors that should NOT be retried (permanent failures)
PERMANENT_ERRORS = [
    "is not available",
    "Private video",
    "Video unavailable",
    "has been removed",
    "account associated with this video has been terminated",
    "copyright",
    "This video requires payment",
    "Sign in to confirm your age",
    "Join this channel to get access",
    "is a private video",
    "Premieres in",
    "This live event will begin",
    "members-only content",
    "blocked it in your country",
    "who has blocked it on copyright grounds",
]

# ── FORMAT LISTS ─────────────────────────────────────────────────────────────

VIDEO_FORMATS = [
    ("mp4",  "MP4  — Universal, plays everywhere"),
    ("mkv",  "MKV  — High quality, flexible containers"),
    ("webm", "WebM — Web-optimized, smaller size"),
    ("avi",  "AVI  — Legacy, wide compatibility"),
    ("mov",  "MOV  — Apple/QuickTime native"),
    ("flv",  "FLV  — Flash Video (legacy)"),
    ("ts",   "TS   — Transport Stream (broadcast)"),
]

AUDIO_FORMATS = [
    ("mp3",  "MP3  — Universal audio, good compression"),
    ("aac",  "AAC  — Better quality than MP3 at same bitrate"),
    ("flac", "FLAC — Lossless, large files, perfect quality"),
    ("wav",  "WAV  — Uncompressed, studio quality"),
    ("opus", "Opus — Modern, best compression-to-quality"),
    ("ogg",  "OGG  — Open format, good quality"),
    ("m4a",  "M4A  — Apple/iTunes standard"),
    ("alac", "ALAC — Apple Lossless"),
    ("aiff", "AIFF — Uncompressed, Apple-native"),
    ("wma",  "WMA  — Windows Media Audio"),
]

RESOLUTION_OPTIONS = [
    ("best",  "Best available quality"),
    ("2160",  "4K   (2160p)"),
    ("1440",  "QHD  (1440p)"),
    ("1080",  "FHD  (1080p)"),
    ("720",   "HD   (720p)"),
    ("480",   "SD   (480p)"),
    ("360",   "Low  (360p)"),
    ("worst", "Worst (smallest file)"),
]

# ── COLORS (ANSI) ───────────────────────────────────────────────────────────

class C:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    RED     = "\033[91m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    BLUE    = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN    = "\033[96m"
    WHITE   = "\033[97m"

# ── LOGGING ──────────────────────────────────────────────────────────────────

LOG_FILE = None

def init_logging():
    global LOG_FILE
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    LOG_FILE = LOGS_DIR / f"session_{timestamp}.log"

def log(msg, level="INFO", color=None):
    """Print to terminal with color and write to log file."""
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
    plain = f"[{timestamp}] [{level}] {msg}"

    color_map = {
        "INFO": C.WHITE,
        "OK": C.GREEN,
        "WARN": C.YELLOW,
        "ERROR": C.RED,
        "DOWNLOAD": C.CYAN,
        "UPDATE": C.MAGENTA,
        "HEADER": C.BOLD + C.BLUE,
    }
    c = color or color_map.get(level, C.WHITE)
    print(f"{c}[{timestamp}] [{level}]{C.RESET} {msg}")

    if LOG_FILE:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(plain + "\n")

def banner():
    print(f"""
{C.BOLD}{C.CYAN}╔══════════════════════════════════════════════════════╗
║               {C.WHITE}MediaGrabber v{APP_VERSION}{C.CYAN}                     ║
║          Portable Media Downloader + Auto-Update     ║
╚══════════════════════════════════════════════════════╝{C.RESET}
""")

# ── UTILITY ──────────────────────────────────────────────────────────────────

def download_file(url, dest, desc="file", max_retries=3):
    """Download a file with progress indication and retry on connection errors."""
    dest_path = Path(dest)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    for attempt in range(1, max_retries + 1):
        try:
            if attempt > 1:
                wait = attempt * 3
                log(f"Retry {attempt}/{max_retries} for {desc} in {wait}s...", "WARN")
                time.sleep(wait)
            else:
                log(f"Downloading {desc}...", "UPDATE")

            req = Request(url, headers={"User-Agent": "MediaGrabber/1.0"})
            with urlopen(req, timeout=180) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                chunk_size = 1024 * 256

                with open(dest_path, "wb") as f:
                    while True:
                        chunk = resp.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            pct = downloaded * 100 // total
                            mb = downloaded / (1024 * 1024)
                            total_mb = total / (1024 * 1024)
                            print(f"\r  {C.DIM}{mb:.1f}/{total_mb:.1f} MB ({pct}%){C.RESET}", end="", flush=True)
                print()

            # Verify we got the full file (if server told us the size)
            if total > 0 and downloaded < total:
                log(f"Incomplete download ({downloaded}/{total} bytes), retrying...", "WARN")
                continue

            log(f"{desc} downloaded OK ({downloaded / (1024*1024):.1f} MB)", "OK")
            return True

        except (ConnectionResetError, ConnectionAbortedError, OSError) as e:
            print()  # clear progress line
            log(f"Connection error downloading {desc}: {e}", "WARN")
            if attempt == max_retries:
                log(f"Failed to download {desc} after {max_retries} attempts.", "ERROR")
                # Clean up partial file
                try:
                    dest_path.unlink(missing_ok=True)
                except Exception:
                    pass
                return False
            continue

        except Exception as e:
            print()
            log(f"Failed to download {desc}: {e}", "ERROR")
            try:
                dest_path.unlink(missing_ok=True)
            except Exception:
                pass
            return False

    return False

def fetch_json(url):
    req = Request(url, headers={"User-Agent": "MediaGrabber/1.0"})
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())

def load_versions():
    if VERSION_FILE.exists():
        with open(VERSION_FILE, "r") as f:
            return json.load(f)
    return {}

def save_versions(versions):
    VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(VERSION_FILE, "w") as f:
        json.dump(versions, f, indent=2)

def load_config():
    defaults = {
        "mode": "video",
        "video_format": "mp4",
        "audio_format": "mp3",
        "resolution": "best",
        "output_dir": OUTPUT_DIR,
        "filename_template": "%(title)s.%(ext)s",
        "auto_update": True,
        "max_retries": 3,
        # Browser to borrow login cookies from (Instagram/TikTok auth).
        # "auto" = detect installed browser, "none" = disable.
        "cookies_browser": "auto",
        # Carousel folders are named from the first N words of the post caption
        "folder_name_words": 4,
    }
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r") as f:
                saved = json.load(f)
            defaults.update(saved)
        except Exception:
            pass
    return defaults

def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

# ── PICKER HELPERS ───────────────────────────────────────────────────────────

def pick_from_list(title, items, current=None):
    """Generic numbered picker. items = [(value, label), ...]. Returns value or None."""
    print(f"\n  {C.BOLD}{C.CYAN}{title}{C.RESET}")
    print(f"  {C.DIM}{'─' * 45}{C.RESET}")
    for i, (val, label) in enumerate(items, 1):
        marker = f" {C.GREEN}<- current{C.RESET}" if val == current else ""
        print(f"  {C.GREEN}[{i:>2}]{C.RESET} {label}{marker}")
    print(f"  {C.DIM}[0]  Cancel{C.RESET}")
    print()

    try:
        choice = input(f"  {C.CYAN}#{C.RESET} ").strip()
        if not choice or choice == "0":
            return None
        idx = int(choice) - 1
        if 0 <= idx < len(items):
            return items[idx][0]
        log("Invalid selection.", "WARN")
        return None
    except (ValueError, EOFError):
        return None

def prompt_resolution(url):
    """Ask user to pick resolution for a video URL. Returns resolution string or None."""
    print(f"\n  {C.BOLD}{C.CYAN}Select Resolution for this download:{C.RESET}")
    print(f"  {C.DIM}URL: {url[:80]}{'...' if len(url) > 80 else ''}{C.RESET}")
    print(f"  {C.DIM}{'─' * 45}{C.RESET}")
    for i, (val, label) in enumerate(RESOLUTION_OPTIONS, 1):
        print(f"  {C.GREEN}[{i:>2}]{C.RESET} {label}")
    print(f"  {C.DIM}[ 0] Use default ({C.RESET}best{C.DIM}){C.RESET}")
    print()

    try:
        choice = input(f"  {C.CYAN}#{C.RESET} ").strip()
        if not choice or choice == "0":
            return "best"
        idx = int(choice) - 1
        if 0 <= idx < len(RESOLUTION_OPTIONS):
            picked = RESOLUTION_OPTIONS[idx]
            log(f"Resolution: {picked[1]}", "OK")
            return picked[0]
        return "best"
    except (ValueError, EOFError):
        return "best"

def change_output_folder(cfg):
    """Let user type a new output folder path or browse."""
    current = cfg.get("output_dir", OUTPUT_DIR)
    print(f"\n  {C.BOLD}{C.CYAN}Change Output Folder{C.RESET}")
    print(f"  {C.DIM}Current: {current}{C.RESET}")
    print()
    print(f"  {C.GREEN}[1]{C.RESET} Type a new path")
    print(f"  {C.GREEN}[2]{C.RESET} Browse with folder picker")
    print(f"  {C.GREEN}[3]{C.RESET} Reset to default")
    print(f"  {C.DIM}[0] Cancel{C.RESET}")
    print()

    choice = input(f"  {C.CYAN}#{C.RESET} ").strip()

    if choice == "1":
        new_path = input(f"  {C.CYAN}New path:{C.RESET} ").strip().strip('"')
        if not new_path:
            log("No path entered.", "WARN")
            return
        p = Path(new_path)
        try:
            p.mkdir(parents=True, exist_ok=True)
            cfg["output_dir"] = str(p)
            save_config(cfg)
            log(f"Output folder changed to: {p}", "OK")
        except Exception as e:
            log(f"Invalid path: {e}", "ERROR")

    elif choice == "2":
        if not IS_WIN:
            log("Folder picker is Windows-only — use option [1] to type the path.", "WARN")
            return
        # Use PowerShell folder picker dialog
        try:
            ps_cmd = (
                'Add-Type -AssemblyName System.Windows.Forms; '
                '$f = New-Object System.Windows.Forms.FolderBrowserDialog; '
                f'$f.SelectedPath = "{current}"; '
                '$f.Description = "Select download output folder"; '
                'if ($f.ShowDialog() -eq "OK") { $f.SelectedPath } else { "" }'
            )
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                capture_output=True, text=True, timeout=120,
                creationflags=NO_WINDOW,
            )
            picked = result.stdout.strip()
            if picked:
                cfg["output_dir"] = picked
                save_config(cfg)
                log(f"Output folder changed to: {picked}", "OK")
            else:
                log("No folder selected.", "WARN")
        except Exception as e:
            log(f"Folder picker failed: {e}", "ERROR")
            log("Use option [1] to type the path instead.", "INFO")

    elif choice == "3":
        cfg["output_dir"] = OUTPUT_DIR
        save_config(cfg)
        log(f"Output folder reset to default: {OUTPUT_DIR}", "OK")

# ── AUTO-UPDATE ──────────────────────────────────────────────────────────────

def update_ytdlp():
    versions = load_versions()
    local_ver = versions.get("yt-dlp", "none")

    try:
        log("Checking yt-dlp for updates...", "UPDATE")
        data = fetch_json(YTDLP_RELEASE_API)
        remote_ver = data["tag_name"]

        if local_ver == remote_ver and YTDLP_EXE.exists():
            log(f"yt-dlp is up to date ({remote_ver})", "OK")
            return True

        log(f"Updating yt-dlp: {local_ver} -> {remote_ver}", "UPDATE")
        if download_file(YTDLP_DOWNLOAD_URL, YTDLP_EXE, YTDLP_EXE.name):
            _make_executable(YTDLP_EXE)
            versions["yt-dlp"] = remote_ver
            save_versions(versions)
            return True
        return False
    except Exception as e:
        if YTDLP_EXE.exists():
            log(f"Update check failed ({e}), using existing yt-dlp", "WARN")
            return True
        log(f"Cannot fetch yt-dlp and no local copy exists: {e}", "ERROR")
        return False

def update_ffmpeg():
    versions = load_versions()
    local_ver = versions.get("ffmpeg", "none")

    try:
        log("Checking ffmpeg for updates...", "UPDATE")
        data = fetch_json(FFMPEG_RELEASE_API)
        # BtbN uses a rolling "latest" tag, so tag_name is useless.
        # Use the published_at timestamp as the real version identifier.
        remote_ver = data.get("published_at", data["tag_name"])

        if local_ver == remote_ver and FFMPEG_EXE.exists() and FFPROBE_EXE.exists():
            log(f"ffmpeg is up to date ({remote_ver})", "OK")
            return True

        log(f"Updating ffmpeg: {local_ver} -> {remote_ver}", "UPDATE")

        # Windows builds ship as .zip, Linux builds as .tar.xz
        want_os = "win64" if IS_WIN else "linux64"
        want_ext = ".zip" if IS_WIN else ".tar.xz"
        wanted_names = ("ffmpeg.exe", "ffprobe.exe") if IS_WIN else ("ffmpeg", "ffprobe")

        asset_url = None
        for asset in data.get("assets", []):
            name = asset["name"]
            if want_os in name and "gpl" in name and name.endswith(want_ext) and "shared" not in name:
                asset_url = asset["browser_download_url"]
                break

        if not asset_url:
            for asset in data.get("assets", []):
                name = asset["name"]
                if want_os in name and name.endswith(want_ext):
                    asset_url = asset["browser_download_url"]
                    break

        if not asset_url:
            log(f"Could not find ffmpeg download for {want_os}", "ERROR")
            return FFMPEG_EXE.exists()

        pkg_path = TOOLS_DIR / f"ffmpeg_temp{want_ext}"
        if not download_file(asset_url, pkg_path, "ffmpeg package"):
            return FFMPEG_EXE.exists()

        log("Extracting ffmpeg binaries...", "UPDATE")
        extracted = False
        if IS_WIN:
            with zipfile.ZipFile(pkg_path, "r") as zf:
                for name in zf.namelist():
                    basename = os.path.basename(name)
                    if basename in wanted_names:
                        target = TOOLS_DIR / basename
                        with zf.open(name) as src, open(target, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                        log(f"  Extracted {basename}", "OK")
                        extracted = True
        else:
            with tarfile.open(pkg_path, "r:xz") as tf:
                for member in tf.getmembers():
                    basename = os.path.basename(member.name)
                    if basename in wanted_names and member.isfile():
                        target = TOOLS_DIR / basename
                        with tf.extractfile(member) as src, open(target, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                        _make_executable(target)
                        log(f"  Extracted {basename}", "OK")
                        extracted = True

        try:
            pkg_path.unlink()
        except Exception:
            pass

        if extracted:
            versions["ffmpeg"] = remote_ver
            save_versions(versions)
            log(f"ffmpeg updated to {remote_ver}", "OK")
            return True
        else:
            log("Could not find ffmpeg.exe in the archive", "ERROR")
            return FFMPEG_EXE.exists()

    except Exception as e:
        if FFMPEG_EXE.exists():
            log(f"Update check failed ({e}), using existing ffmpeg", "WARN")
            return True
        log(f"Cannot fetch ffmpeg and no local copy exists: {e}", "ERROR")
        return False

def update_deno():
    """Check and update Deno (required by yt-dlp for YouTube JS extraction)."""
    versions = load_versions()
    local_ver = versions.get("deno", "none")

    try:
        log("Checking Deno for updates...", "UPDATE")
        data = fetch_json(DENO_RELEASE_API)
        remote_ver = data["tag_name"]

        if local_ver == remote_ver and DENO_EXE.exists():
            log(f"Deno is up to date ({remote_ver})", "OK")
            return True

        log(f"Updating Deno: {local_ver} -> {remote_ver}", "UPDATE")

        # Find the x64 zip for this OS
        want_os = "windows" if IS_WIN else "linux"
        asset_url = None
        for asset in data.get("assets", []):
            name = asset["name"].lower()
            if "x86_64" in name and want_os in name and name.endswith(".zip"):
                asset_url = asset["browser_download_url"]
                break

        if not asset_url:
            log(f"Could not find Deno download for {want_os} x64", "ERROR")
            return DENO_EXE.exists()

        zip_path = TOOLS_DIR / "deno_temp.zip"
        if not download_file(asset_url, zip_path, "Deno"):
            return DENO_EXE.exists()

        log("Extracting Deno...", "UPDATE")
        extracted = False
        with zipfile.ZipFile(zip_path, "r") as zf:
            for name in zf.namelist():
                if os.path.basename(name).lower() == DENO_EXE.name.lower():
                    with zf.open(name) as src, open(DENO_EXE, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    _make_executable(DENO_EXE)
                    log(f"  Extracted {DENO_EXE.name}", "OK")
                    extracted = True
                    break

        try:
            zip_path.unlink()
        except Exception:
            pass

        if extracted:
            versions["deno"] = remote_ver
            save_versions(versions)
            log(f"Deno updated to {remote_ver}", "OK")
            return True
        else:
            log("Could not find deno.exe in the archive", "ERROR")
            return DENO_EXE.exists()

    except Exception as e:
        if DENO_EXE.exists():
            log(f"Update check failed ({e}), using existing Deno", "WARN")
            return True
        log(f"Cannot fetch Deno and no local copy exists: {e}", "ERROR")
        return False

def update_gallerydl():
    """Check and update gallery-dl (used for image/carousel posts)."""
    versions = load_versions()
    local_ver = versions.get("gallery-dl", "none")

    try:
        log("Checking gallery-dl for updates...", "UPDATE")
        data = fetch_json(GALLERYDL_RELEASE_API)
        remote_ver = data["tag_name"]

        if local_ver == remote_ver and GALLERYDL_EXE.exists():
            log(f"gallery-dl is up to date ({remote_ver})", "OK")
            return True

        log(f"Updating gallery-dl: {local_ver} -> {remote_ver}", "UPDATE")

        # x64 builds: gallery-dl_windows.exe / gallery-dl_linux
        want = "gallery-dl_windows.exe" if IS_WIN else "gallery-dl_linux"
        asset_url = None
        for asset in data.get("assets", []):
            if asset["name"].lower() == want:
                asset_url = asset["browser_download_url"]
                break

        if not asset_url:
            log(f"Could not find {want} in the release", "ERROR")
            return GALLERYDL_EXE.exists()

        if download_file(asset_url, GALLERYDL_EXE, GALLERYDL_EXE.name):
            _make_executable(GALLERYDL_EXE)
            versions["gallery-dl"] = remote_ver
            save_versions(versions)
            return True
        return GALLERYDL_EXE.exists()

    except Exception as e:
        if GALLERYDL_EXE.exists():
            log(f"Update check failed ({e}), using existing gallery-dl", "WARN")
            return True
        log(f"Cannot fetch gallery-dl and no local copy exists: {e}", "ERROR")
        return False

def tools_present():
    return all(t.exists() for t in
               (YTDLP_EXE, FFMPEG_EXE, FFPROBE_EXE, DENO_EXE, GALLERYDL_EXE))

def run_updates(cfg, force=False):
    """Check/download tools. Throttled: skips entirely if all tools exist and
       the last check was under UPDATE_INTERVAL_DAYS ago, so users can start
       downloading immediately. force=True (menu [7], or after a tool failure)
       always checks."""
    if not force:
        if not cfg.get("auto_update", True):
            log("Auto-update disabled in config", "WARN")
            if tools_present():
                return True
            log("Tools missing — forcing update despite config", "WARN")
        elif tools_present():
            versions = load_versions()
            age = time.time() - versions.get("_last_check", 0)
            if age < UPDATE_INTERVAL_DAYS * 86400:
                days_left = UPDATE_INTERVAL_DAYS - age / 86400
                log(f"Tools ready — next update check in {max(days_left, 0):.0f} day(s) (menu [7] to force)", "OK")
                return True

    ok = True
    if not update_ytdlp():
        ok = False
    if not update_ffmpeg():
        ok = False
    if not update_deno():
        ok = False
    if not update_gallerydl():
        ok = False

    versions = load_versions()
    versions["_last_check"] = time.time()
    save_versions(versions)
    return ok

# ── LOGIN / COOKIES (auth via browser session) ───────────────────────────────
# Instead of storing a username/password (which Instagram blocks for scripts
# and which breaks with 2FA), we borrow the login session cookies from a
# browser where the user is already logged in. Both yt-dlp and gallery-dl
# support this natively via --cookies-from-browser.

def _browser_profile_paths():
    if IS_WIN:
        local = os.environ.get("LOCALAPPDATA", "")
        roaming = os.environ.get("APPDATA", "")
        return {
            "zen":     Path(roaming) / "zen" / "Profiles",
            "firefox": Path(roaming) / "Mozilla" / "Firefox" / "Profiles",
            "chrome":  Path(local) / "Google" / "Chrome" / "User Data",
            "edge":    Path(local) / "Microsoft" / "Edge" / "User Data",
            "brave":   Path(local) / "BraveSoftware" / "Brave-Browser" / "User Data",
            "vivaldi": Path(local) / "Vivaldi" / "User Data",
            "opera":   Path(roaming) / "Opera Software" / "Opera Stable",
        }
    home = Path.home()
    return {
        "zen":     home / ".zen",
        "firefox": home / ".mozilla" / "firefox",
        "chrome":  home / ".config" / "google-chrome",
        "edge":    home / ".config" / "microsoft-edge",
        "brave":   home / ".config" / "BraveSoftware" / "Brave-Browser",
        "vivaldi": home / ".config" / "vivaldi",
        "opera":   home / ".config" / "opera",
    }

# Firefox-family first (Zen, Firefox): Chromium browsers use cookie DB locks /
# app-bound encryption which can block extraction while the browser is running.
BROWSER_ORDER = ["zen", "firefox", "chrome", "edge", "brave", "vivaldi", "opera"]

def _zen_profile():
    """Locate the active Zen Browser profile (the one with the freshest cookies)."""
    root = _browser_profile_paths()["zen"]
    if not root.exists():
        return None
    cands = [p for p in root.iterdir() if p.is_dir() and (p / "cookies.sqlite").exists()]
    if not cands:
        return None
    return max(cands, key=lambda p: (p / "cookies.sqlite").stat().st_mtime)

def cookie_browser_spec(browser):
    """Translate our browser name into the spec yt-dlp/gallery-dl understand.
       Zen is Firefox-based but unknown to the tools, so we pass its profile
       path explicitly as firefox:<path>. Returns None if unavailable."""
    if browser == "zen":
        prof = _zen_profile()
        return f"firefox:{prof}" if prof else None
    return browser

def detect_installed_browsers():
    paths = _browser_profile_paths()
    return [b for b in BROWSER_ORDER if paths.get(b) and paths[b].exists()]

def resolve_cookie_browser(cfg):
    """Return the browser name to pull login cookies from, or None."""
    b = cfg.get("cookies_browser", "auto")
    if b == "none":
        return None
    if b != "auto":
        return b
    found = detect_installed_browsers()
    return found[0] if found else None

def needs_login(url):
    """Sites where downloads commonly require an authenticated session."""
    return bool(re.search(r"(instagram\.com|tiktok\.com)", url, re.IGNORECASE))

def cookie_cache_valid():
    return COOKIES_FILE.exists() and COOKIES_FILE.stat().st_size > 0

def cookie_args(cfg):
    """Cookie arguments for yt-dlp / gallery-dl (both accept the same flags).
       Prefer the cached cookie file — it works even while the browser is
       open and holding a lock on its cookie database."""
    if cookie_cache_valid():
        return ["--cookies", str(COOKIES_FILE)]
    spec = cookie_browser_spec(resolve_cookie_browser(cfg))
    if spec:
        return ["--cookies-from-browser", spec]
    return []

# Output markers that mean the browser is locking its cookie database
COOKIE_LOCK_MARKERS = ["could not copy", "permission denied", "errno 13"]

def refresh_cookie_cache(cfg, interactive=True):
    """Export browser login cookies to tools/cookies.txt so downloads keep
       working while the browser is open (Chromium locks its cookie DB).
       Returns True if a usable cache exists afterwards."""
    b = resolve_cookie_browser(cfg)
    spec = cookie_browser_spec(b) if b else None
    if not spec:
        if b == "zen":
            log("Zen Browser profile with cookies not found — is Zen installed and used?", "ERROR")
        return cookie_cache_valid()

    for attempt in (1, 2):
        log(f"Exporting login cookies from {b}...", "UPDATE")
        rc, out = _run_quiet(
            [str(YTDLP_EXE), "--cookies-from-browser", spec,
             "--cookies", str(COOKIES_FILE),
             "--flat-playlist", "--playlist-items", "1", "--simulate",
             "--no-warnings",
             "https://www.instagram.com/instagram/"],
            timeout=120,
        )
        low = out.lower()
        locked = any(m in low for m in COOKIE_LOCK_MARKERS)
        if not locked and cookie_cache_valid():
            log(f"Login cookies cached: {COOKIES_FILE.name} (source: {b})", "OK")
            return True
        if locked:
            log(f"{b.title()} is locking its cookie database — cookies can't be read while it runs.", "WARN")
            if interactive and attempt == 1:
                try:
                    input(f"  Close {b.title()} COMPLETELY (incl. tray icon), then press Enter to retry... ")
                    continue
                except EOFError:
                    pass
        break

    if cookie_cache_valid():
        log("Using previously cached login cookies (may be stale).", "WARN")
        return True
    log("Could not export login cookies.", "ERROR")
    return False

def choose_cookie_browser(cfg):
    """Menu: pick which browser to borrow login cookies from."""
    found = detect_installed_browsers()
    items = [(b, f"{b.title():<8}{' (installed)' if b in found else ''}") for b in BROWSER_ORDER]
    items.append(("auto", "Auto-detect (prefer Firefox)"))
    items.append(("none", "Disable login cookies"))
    picked = pick_from_list("Login Cookie Browser", items, cfg.get("cookies_browser", "auto"))
    if picked:
        cfg["cookies_browser"] = picked
        save_config(cfg)
        log(f"Login cookies will be read from: {picked}", "OK")
        # Old cache belongs to the previous browser — rebuild it now
        try:
            COOKIES_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        if picked != "none":
            refresh_cookie_cache(cfg)

# ── STARTUP CHECKUP ──────────────────────────────────────────────────────────

def _run_quiet(args, timeout=60):
    """Run a command, return (returncode, combined_output)."""
    try:
        r = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
            creationflags=NO_WINDOW,
        )
        return (r.returncode, (r.stdout or "") + (r.stderr or ""))
    except Exception as e:
        return (-1, str(e))

def run_checkup(cfg, probe_auth=True, quick=False):
    """Verify all tools run and the Instagram login session works.
       quick=True (cold start): existence checks only, and the (slow) live
       Instagram probe runs only when no cookie cache exists yet."""
    log("Running system checkup..." + (" (quick)" if quick else ""), "HEADER")
    ok = True

    tools = [
        ("yt-dlp",     YTDLP_EXE,     ["--version"]),
        ("ffmpeg",     FFMPEG_EXE,    ["-version"]),
        ("ffprobe",    FFPROBE_EXE,   ["-version"]),
        ("deno",       DENO_EXE,      ["--version"]),
        ("gallery-dl", GALLERYDL_EXE, ["--version"]),
    ]
    for name, exe, vargs in tools:
        if not exe.exists():
            log(f"  [MISSING] {name} — run menu [7] to install", "ERROR")
            ok = False
            continue
        if quick:
            log(f"  [OK] {name}: installed", "OK")
            continue
        rc, out = _run_quiet([str(exe)] + vargs, timeout=30)
        if rc == 0:
            ver = out.strip().splitlines()[0][:60] if out.strip() else "ok"
            log(f"  [OK] {name}: {ver}", "OK")
        else:
            log(f"  [FAIL] {name} did not run: {out.strip()[:100]}", "ERROR")
            ok = False

    if quick:
        probe_auth = probe_auth and not cookie_cache_valid()

    # ── Login / auth check ──
    browser = resolve_cookie_browser(cfg)
    if browser is None and not cookie_cache_valid():
        log("  [WARN] No login cookie source — Instagram carousels will fail.", "WARN")
        log("         Set one via menu [11].", "INFO")
        ok = False
    else:
        # Make sure we have an exported cookie cache (works while browser runs)
        if not cookie_cache_valid():
            refresh_cookie_cache(cfg)
        if cookie_cache_valid():
            log(f"  Login cookies: cached file ({COOKIES_FILE.name}, source: {browser})", "INFO")
        else:
            log(f"  Login cookies: live from {browser} (no cache — may fail while browser is open)", "WARN")

        if probe_auth and GALLERYDL_EXE.exists():
            for probe_round in (1, 2):
                log("  Probing Instagram login session...", "INFO")
                rc, out = _run_quiet(
                    [str(GALLERYDL_EXE)] + cookie_args(cfg) +
                    ["--simulate", "--range", "1",
                     "https://www.instagram.com/instagram/"],
                    timeout=90,
                )
                low = out.lower()
                if "login" in low or "unauthorized" in low or "401" in low:
                    if probe_round == 1 and cookie_cache_valid():
                        # Cached cookies may be stale — rebuild and try once more
                        log("  Cached cookies look stale — refreshing from browser...", "WARN")
                        try:
                            COOKIES_FILE.unlink(missing_ok=True)
                        except Exception:
                            pass
                        if refresh_cookie_cache(cfg):
                            continue
                    log(f"  [FAIL] Instagram session not logged in (source: {browser}).", "ERROR")
                    log(f"         Log in at instagram.com in {browser}, then re-run checkup (menu [10]).", "INFO")
                    ok = False
                elif rc == 0:
                    log("  [OK] Instagram login session works.", "OK")
                else:
                    tail = out.strip().splitlines()[-1][:100] if out.strip() else "no output"
                    log(f"  [WARN] Auth probe inconclusive: {tail}", "WARN")
                    if any(m in low for m in COOKIE_LOCK_MARKERS):
                        log(f"         {browser.title()} is locking its cookie DB — close it fully and re-run checkup (menu [10]).", "INFO")
                break

    if ok:
        log("Checkup passed — everything is ready.", "OK")
    else:
        log("Checkup found issues — see above.", "WARN")
    return ok

# ── DOWNLOAD ENGINE ──────────────────────────────────────────────────────────

# Matches Instagram posts (may be carousels) and TikTok photo-mode posts
CAROUSEL_URL_RE = re.compile(
    r"(instagram\.com/(p|reel)/|tiktok\.com/.+/photo/|vt\.tiktok\.com/)",
    re.IGNORECASE,
)

def is_carousel_candidate(url):
    """True if URL may be a multi-item carousel (IG post / TikTok photo post)."""
    return bool(CAROUSEL_URL_RE.search(url))

def carousel_folder_name(url):
    """Derive a subfolder name from the carousel link itself.
       e.g. https://www.instagram.com/p/ABC123/  -> instagram_ABC123
            https://www.tiktok.com/@user/photo/7291 -> tiktok_7291"""
    m = re.search(r"instagram\.com/(?:p|reel)/([A-Za-z0-9_-]+)", url, re.IGNORECASE)
    if m:
        return f"instagram_{m.group(1)}"
    m = re.search(r"tiktok\.com/@([^/]+)/photo/(\d+)", url, re.IGNORECASE)
    if m:
        return f"tiktok_{m.group(1)}_{m.group(2)}"
    m = re.search(r"vt\.tiktok\.com/([A-Za-z0-9]+)", url, re.IGNORECASE)
    if m:
        return f"tiktok_{m.group(1)}"
    # Fallback: last meaningful path segment, sanitized
    seg = re.sub(r"[?#].*$", "", url).rstrip("/").rsplit("/", 1)[-1]
    seg = re.sub(r'[<>:"/\\|?*]', "_", seg)[:80] or "carousel"
    return seg

def _clean_caption_words(caption, max_words):
    """Turn a post caption into a filesystem-safe folder name from its
       first few words. Strips URLs, hashtags, mentions, emojis."""
    text = re.sub(r"https?://\S+", " ", caption)
    text = re.sub(r"[#@]\S+", " ", text)                 # hashtags & mentions
    text = re.sub(r"[^\w\s'\-]", " ", text)              # emojis & punctuation
    words = [w for w in text.split() if w]
    if not words:
        return None
    name = " ".join(words[:max_words])
    name = re.sub(r'[<>:"/\\|?*{}]', "", name).strip(" .-")
    return name[:60] or None

def probe_post(url, cfg):
    """Probe a post via gallery-dl metadata (no media download).
       Returns (media_count, caption); (None, None) if the probe failed."""
    if not GALLERYDL_EXE.exists():
        return (None, None)
    rc, out = _run_quiet(
        [str(GALLERYDL_EXE)] + cookie_args(cfg) + ["-j", url],
        timeout=90,
    )
    if not out:
        return (None, None)
    # gallery-dl -j emits a (pretty-printed, multi-line) JSON array, possibly
    # preceded by warning lines like "[instagram][warning] ...". Try to decode
    # JSON starting at each "[" until one parses.
    data = None
    decoder = json.JSONDecoder()
    for m in list(re.finditer(r"\[", out))[:25]:
        try:
            candidate, _ = decoder.raw_decode(out[m.start():])
        except Exception:
            continue
        if isinstance(candidate, list) and candidate:
            data = candidate
            break
    if data is None:
        return (None, None)

    # Entries are [msg_type, ...]; msg_type 3 = one downloadable file
    count = sum(1 for e in data if isinstance(e, list) and e and e[0] == 3)

    def scan(obj):
        if isinstance(obj, dict):
            for key in ("description", "content", "desc", "title"):
                v = obj.get(key)
                if isinstance(v, str) and v.strip():
                    return v
            for v in obj.values():
                r = scan(v)
                if r:
                    return r
        elif isinstance(obj, list):
            for v in obj:
                r = scan(v)
                if r:
                    return r
        return None

    return (count if count > 0 else None, scan(data))

def post_base_name(url, caption, cfg):
    """Human-friendly base name for a post: first N caption words,
       falling back to the link-based name."""
    if caption:
        name = _clean_caption_words(caption, int(cfg.get("folder_name_words", 4)))
        if name:
            return name
    return carousel_folder_name(url)

def build_ytdlp_args(url, cfg, resolution_override=None, out_dir_override=None):
    """Build yt-dlp command arguments for a URL."""
    args = [str(YTDLP_EXE)]

    # Tool locations
    args += ["--ffmpeg-location", str(TOOLS_DIR)]
    if DENO_EXE.exists():
        args += ["--js-runtimes", f"deno:{DENO_EXE}"]

    # Output — download to a temp staging name, then rename after to avoid collisions
    out_dir = out_dir_override or cfg.get("output_dir", OUTPUT_DIR)
    args += ["-P", str(out_dir)]
    # Use a unique temp name so yt-dlp never sees an existing file and skips/overwrites.
    # The __MGIDX_n__ marker carries the playlist index (0 = single item) so that
    # carousel posts (multiple files, same title) never collide and can be
    # grouped into their own folder afterwards.
    args += ["-o", "%(title)s.__MGIDX_%(playlist_index|0)s__._MGTMP_.%(ext)s"]
    args += ["--no-part"]  # write directly (no .part suffix) so we know the exact filename

    carousel = is_carousel_candidate(url)
    if carousel:
        args += ["--yes-playlist"]

    # Login cookies for sites that need an authenticated session (IG/TikTok)
    if needs_login(url):
        args += cookie_args(cfg)

    # Mode
    mode = cfg.get("mode", "video")
    if mode == "audio":
        args += ["-x", "--audio-format", cfg.get("audio_format", "mp3")]
    elif carousel:
        # Carousels can mix images and videos — strict video format selectors
        # and --recode-video would fail on image entries. Take best as-is.
        args += ["-f", "best"]
    else:
        # Video format
        vfmt = cfg.get("video_format", "mp4")
        args += ["--recode-video", vfmt]

        # Resolution
        res = resolution_override or cfg.get("resolution", "best")
        if res == "best":
            args += ["-f", "bestvideo+bestaudio/best"]
        elif res == "worst":
            args += ["-f", "worstvideo+worstaudio/worst"]
        else:
            # Request specific height cap — fall back gracefully
            args += ["-f", f"bestvideo[height<={res}]+bestaudio/best[height<={res}]/best"]

    # Retries
    args += ["--retries", str(cfg.get("max_retries", 3))]

    # No mtime
    args += ["--no-mtime"]

    # Progress
    args += ["--newline"]

    args.append(url)
    return args

_TMP_RE = re.compile(r"^(?P<title>.+)\.__MGIDX_(?P<idx>\d*)__\._MGTMP_\.(?P<ext>.+)$")

def _unique_path(parent, name):
    """Return a non-existing path in parent based on name, adding (1), (2)... if needed."""
    candidate = parent / name
    if not candidate.exists():
        return candidate
    p = Path(name)
    stem, ext = p.stem, p.suffix
    counter = 1
    while True:
        candidate = parent / f"{stem} ({counter}){ext}"
        if not candidate.exists():
            return candidate
        counter += 1

def _safe_rename(filepath, target_name=None, target_dir=None):
    """Move/rename a temp file to its final name, auto-numbering on collision.
       Returns the final Path."""
    p = Path(filepath)
    name = target_name or p.name
    final = _unique_path(target_dir or p.parent, name)
    p.rename(final)
    return final

def _rename_temp_files(out_dir):
    """Finalize all *._MGTMP_.* files in the given dir (strip temp markers,
    auto-number on collision). Returns list of final paths."""
    out = Path(out_dir)
    tmp_files = sorted(out.glob("*.__MGIDX_*__._MGTMP_.*")) + sorted(out.glob("*._MGTMP_.*"))
    # Deduplicate while keeping order
    seen = set()
    tmp_files = [t for t in tmp_files if not (t in seen or seen.add(t))]

    parsed = []   # (path, title, idx, ext)
    for tmp in tmp_files:
        m = _TMP_RE.match(tmp.name)
        if m:
            idx = int(m.group("idx") or 0)
            parsed.append((tmp, m.group("title"), idx, m.group("ext")))
        else:
            # Legacy temp name without index marker
            parsed.append((tmp, tmp.name.replace("._MGTMP_.", ".", 1).rsplit(".", 1)[0], 0,
                           tmp.suffix.lstrip(".")))

    renamed = []
    for tmp, title, idx, ext in sorted(parsed, key=lambda it: (it[1], it[2])):
        # Carousel items (idx > 0) keep a numeric suffix so they never collide;
        # they already live in the right folder (subfolder is created upfront).
        name = f"{title} - {idx:02d}.{ext}" if idx > 0 else f"{title}.{ext}"
        renamed.append(_safe_rename(tmp, target_name=name))
    return renamed

def _is_permanent_error(output_lines):
    """Check if yt-dlp output contains a permanent (non-retryable) error."""
    combined = " ".join(output_lines[-10:]).lower()
    for pattern in PERMANENT_ERRORS:
        if pattern.lower() in combined:
            return True
    return False

# Errors that usually mean the downloader itself is outdated (sites like
# TikTok/Instagram change frequently; e.g. TikTok's JS challenge breaks old
# yt-dlp versions). These trigger a forced tool update + one free retry.
TOOL_FAILURE_MARKERS = [
    "unable to extract",
    "js challenge",
    "failed to solve",
    "unsupported url",
    "please report this issue",
    "confirm you are on the latest version",
    "http error 403",
]

def _is_tool_failure(output_lines):
    combined = " ".join(output_lines[-15:]).lower()
    return any(m in combined for m in TOOL_FAILURE_MARKERS)

def download_gallerydl(url, target_dir, cfg, tag, base_name=None, single=False):
    """Download a post (images + videos) via gallery-dl into target_dir.
       Files are named '<base_name> - 01.ext' (or '<base_name>.ext' when
       single). Returns (success, n_new_files)."""
    if not GALLERYDL_EXE.exists():
        log(f"{tag} gallery-dl not installed — will fall back to yt-dlp", "WARN")
        return (False, 0)

    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    before = {f.name for f in target_dir.iterdir()}

    # -D forces a flat, exact target directory (no site/user nesting)
    args = [
        str(GALLERYDL_EXE),
        "-D", str(target_dir),
        "--retries", str(cfg.get("max_retries", 3)),
    ]

    # Meaningful filenames instead of gallery-dl's numeric media IDs
    if base_name:
        if single:
            args += ["-f", f"{base_name}.{{extension}}"]
        else:
            args += ["-f", f"{base_name} - {{num:>02}}.{{extension}}"]

    # Login cookies — Instagram redirects to login without an authed session
    cargs = cookie_args(cfg)
    if cargs:
        args += cargs
    elif needs_login(url):
        log(f"{tag} No login cookies available — Instagram may reject this (menu [11] / [10])", "WARN")

    args.append(url)
    log(f"  Target: {target_dir}", "INFO")

    try:
        process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=NO_WINDOW,
        )
        for line in process.stdout:
            if _stop_requested():
                log(f"{tag} Stop requested — cancelling download...", "WARN")
                _stop_process(process)
                raise DownloadStopped()
            line = line.rstrip()
            if not line:
                continue
            if "error" in line.lower() or "warning" in line.lower():
                log(f"  {line}", "ERROR" if "error" in line.lower() else "WARN")
            else:
                log(f"  {line}", "INFO")
        process.wait()
    except DownloadStopped:
        raise
    except Exception as e:
        log(f"{tag} gallery-dl crashed: {e}", "ERROR")
        return (False, 0)

    new_files = [f for f in target_dir.iterdir() if f.is_file() and f.name not in before]
    if process.returncode == 0 and new_files:
        return (True, len(new_files))
    return (False, len(new_files))

def download_single(url, cfg, index, total, resolution_override=None):
    """Download a single URL with smart retry. Returns (url, success, message)."""
    tag = f"[{index}/{total}]"
    max_attempts = cfg.get("max_retries", 3)
    out_dir = cfg.get("output_dir", OUTPUT_DIR)

    # Self-heal: a missing tool triggers an immediate (forced) update
    if not YTDLP_EXE.exists() or (is_carousel_candidate(url) and not GALLERYDL_EXE.exists()):
        log(f"{tag} Required tool missing — updating tools now...", "WARN")
        run_updates(cfg, force=True)

    # ── IG/TikTok posts: probe first. Real carousels (2+ items) get their own
    #    caption-named subfolder; single posts download normally. ──
    carousel = False
    base_name = None
    if is_carousel_candidate(url):
        log(f"{tag} Probing post metadata...", "INFO")
        count, caption = probe_post(url, cfg)
        base_name = post_base_name(url, caption, cfg)
        if count and count > 1:
            carousel = True
            sub_dir = Path(out_dir) / base_name
            log(f"{tag} Carousel ({count} items) -> {base_name}", "OK")
            ok, n = download_gallerydl(url, sub_dir, cfg, tag, base_name=base_name)
            if ok:
                log(f"{tag} Completed: {n} file(s) -> {sub_dir.name}", "OK")
                return (url, True, f"OK ({n} files in {sub_dir.name})")
            log(f"{tag} gallery-dl failed — falling back to yt-dlp", "WARN")
            out_dir = str(sub_dir)  # yt-dlp fallback also downloads into the subfolder
        else:
            log(f"{tag} Single post — downloading normally", "INFO")

    attempt = 0
    updated_once = False
    while attempt < max_attempts:
        attempt += 1
        if attempt == 1:
            log(f"{tag} Starting: {url}", "DOWNLOAD")
        else:
            log(f"{tag} Retry {attempt - 1}/{max_attempts - 1}...", "WARN")

        args = build_ytdlp_args(url, cfg, resolution_override,
                                out_dir_override=out_dir if carousel else None)

        try:
            process = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=NO_WINDOW,
            )

            output_lines = []
            for line in process.stdout:
                if _stop_requested():
                    print()
                    log(f"{tag} Stop requested — cancelling download...", "WARN")
                    _stop_process(process)
                    raise DownloadStopped()
                line = line.rstrip()
                if not line:
                    continue
                output_lines.append(line)

                if "[download]" in line and "%" in line:
                    print(f"\r  {C.DIM}{line.strip()}{C.RESET}", end="", flush=True)
                elif "[download]" in line and "Destination" in line:
                    print()
                    log(f"  {line.strip()}", "INFO")
                elif any(kw in line for kw in ["[Merger]", "[ExtractAudio]", "[VideoConvertor]", "[ffmpeg]", "[VideoRemuxer]"]):
                    log(f"  {line.strip()}", "INFO")
                elif "ERROR" in line.upper():
                    log(f"  {line.strip()}", "ERROR")

            print()
            process.wait()

            if process.returncode == 0:
                # Rename temp files to final names with auto-numbering
                renamed = _rename_temp_files(out_dir)
                for fp in renamed:
                    log(f"  Saved: {fp.name}", "OK")
                log(f"{tag} Completed: {url}", "OK")
                return (url, True, "OK")

            # ── Failed — decide whether to retry ──
            # Clean up temp files from this attempt
            for tmp in Path(out_dir).glob("*._MGTMP_.*"):
                try:
                    tmp.unlink()
                except Exception:
                    pass

            # Permanent error? Don't waste time retrying.
            if _is_permanent_error(output_lines):
                err_tail = "\n".join(output_lines[-3:])
                log(f"{tag} Permanent failure (no retry): {url}", "ERROR")
                log(f"  {err_tail}", "ERROR")
                return (url, False, "Permanent error — video unavailable/private/blocked")

            # Outdated-tool error (e.g. TikTok JS challenge vs old yt-dlp)?
            # Force-update tools once and retry without consuming an attempt.
            if not updated_once and _is_tool_failure(output_lines):
                log(f"{tag} Failure looks tool-related — force-updating tools and retrying...", "WARN")
                run_updates(cfg, force=True)
                updated_once = True
                attempt -= 1
                continue

            # Transient error — retry if attempts remain
            if attempt < max_attempts:
                time.sleep(3 * attempt)
                continue

            # Out of retries — for single IG/TikTok posts (e.g. image posts
            # yt-dlp can't handle), try gallery-dl as a last resort
            if is_carousel_candidate(url) and not carousel:
                log(f"{tag} yt-dlp failed — trying gallery-dl for this post", "WARN")
                ok, n = download_gallerydl(url, Path(out_dir), cfg, tag,
                                           base_name=base_name, single=True)
                if ok:
                    log(f"{tag} Completed via gallery-dl: {url}", "OK")
                    return (url, True, "OK (gallery-dl)")

            err = "\n".join(output_lines[-5:])
            log(f"{tag} Failed after {max_attempts} attempts: {url}", "ERROR")
            log(f"  Last output: {err}", "ERROR")
            return (url, False, f"Failed after {max_attempts} attempts")

        except DownloadStopped:
            # Clean up partial temp files, then let the caller stop the batch
            for tmp in Path(out_dir).glob("*._MGTMP_.*"):
                try:
                    tmp.unlink()
                except Exception:
                    pass
            raise

        except FileNotFoundError:
            # Tool binary vanished/corrupt — reinstall and retry this attempt
            log(f"{tag} Downloader binary missing — reinstalling tools...", "ERROR")
            run_updates(cfg, force=True)
            continue

        except Exception as e:
            log(f"{tag} Crashed: {url} — {e}", "ERROR")
            return (url, False, str(e))

    return (url, False, "Unknown failure")

# ── URL FILE HANDLING ────────────────────────────────────────────────────────

def read_urls():
    if not URLS_FILE.exists():
        with open(URLS_FILE, "w", encoding="utf-8") as f:
            f.write("# MediaGrabber URL List\n")
            f.write("# Paste one URL per line. Lines starting with # are ignored.\n")
            f.write("# Save this file, then run MediaGrabber.\n\n")
        return []

    urls = []
    with open(URLS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
    return urls

def clear_urls():
    with open(URLS_FILE, "w", encoding="utf-8") as f:
        f.write("# MediaGrabber URL List\n")
        f.write("# Paste one URL per line. Lines starting with # are ignored.\n")
        f.write("# All URLs processed — paste new ones here.\n\n")

# ── MENU ─────────────────────────────────────────────────────────────────────

def format_status(cfg):
    """Build a short string showing current format."""
    mode = cfg.get("mode", "video")
    if mode == "audio":
        return f"AUDIO ({cfg.get('audio_format', 'mp3').upper()})"
    else:
        vfmt = cfg.get("video_format", "mp4").upper()
        res = cfg.get("resolution", "best")
        res_label = res if res in ("best", "worst") else f"{res}p"
        return f"VIDEO ({vfmt} @ {res_label})"

def show_menu(cfg):
    fmt_display = format_status(cfg)
    update_status = "ON" if cfg.get("auto_update", True) else "OFF"
    out_short = cfg.get("output_dir", OUTPUT_DIR)

    print(f"""
{C.BOLD}┌──────────────────────────────────────────────┐
│  {C.CYAN}FORMAT{C.RESET}{C.BOLD}:      {C.WHITE}{fmt_display}{C.RESET}{C.BOLD}
│  {C.CYAN}AUTO-UPDATE{C.RESET}{C.BOLD}: {C.WHITE}{update_status}{C.RESET}{C.BOLD}
│  {C.CYAN}OUTPUT{C.RESET}{C.BOLD}:      {C.DIM}{out_short}{C.RESET}{C.BOLD}
├──────────────────────────────────────────────┤
│  {C.GREEN}[1]{C.RESET}{C.BOLD}  Download from urls.txt                  │
│  {C.GREEN}[2]{C.RESET}{C.BOLD}  Download single URL                     │
│  {C.GREEN}[3]{C.RESET}{C.BOLD}  Change format (Video / Audio)           │
│  {C.GREEN}[4]{C.RESET}{C.BOLD}  Change resolution (Video only)          │
│  {C.GREEN}[5]{C.RESET}{C.BOLD}  Change output folder                    │
│  {C.GREEN}[6]{C.RESET}{C.BOLD}  Toggle auto-update                      │
│  {C.GREEN}[7]{C.RESET}{C.BOLD}  Force update tools now                  │
│  {C.GREEN}[8]{C.RESET}{C.BOLD}  Open output folder                      │
│  {C.GREEN}[9]{C.RESET}{C.BOLD}  Open urls.txt for editing               │
│  {C.GREEN}[10]{C.RESET}{C.BOLD} Checkup (tools + login)                 │
│  {C.GREEN}[11]{C.RESET}{C.BOLD} Set login cookie browser                │
│  {C.GREEN}[12]{C.RESET}{C.BOLD} Delete saved login cookies              │
│  {C.GREEN}[0]{C.RESET}{C.BOLD}  Exit                                    │
└──────────────────────────────────────────────┘{C.RESET}
""")

def choose_format(cfg):
    """Interactive format picker — shows video or audio formats."""
    print(f"\n  {C.BOLD}{C.CYAN}Choose Mode & Format{C.RESET}")
    print(f"  {C.GREEN}[1]{C.RESET} Video formats")
    print(f"  {C.GREEN}[2]{C.RESET} Audio formats")
    print(f"  {C.DIM}[0] Cancel{C.RESET}")
    print()
    choice = input(f"  {C.CYAN}#{C.RESET} ").strip()

    if choice == "1":
        picked = pick_from_list("Video Formats", VIDEO_FORMATS, cfg.get("video_format"))
        if picked:
            cfg["mode"] = "video"
            cfg["video_format"] = picked
            save_config(cfg)
            log(f"Format set to VIDEO ({picked.upper()})", "OK")
    elif choice == "2":
        picked = pick_from_list("Audio Formats", AUDIO_FORMATS, cfg.get("audio_format"))
        if picked:
            cfg["mode"] = "audio"
            cfg["audio_format"] = picked
            save_config(cfg)
            log(f"Format set to AUDIO ({picked.upper()})", "OK")

def choose_resolution(cfg):
    """Set default resolution for video downloads."""
    if cfg.get("mode") == "audio":
        log("Resolution only applies to video mode. Switch to video first.", "WARN")
        return
    picked = pick_from_list("Default Video Resolution", RESOLUTION_OPTIONS, cfg.get("resolution", "best"))
    if picked:
        cfg["resolution"] = picked
        save_config(cfg)
        label = picked if picked in ("best", "worst") else f"{picked}p"
        log(f"Default resolution set to: {label}", "OK")

# ── BATCH / SINGLE DOWNLOAD ─────────────────────────────────────────────────

def process_batch(cfg):
    """Process all URLs from urls.txt."""
    urls = read_urls()
    if not urls:
        log("No URLs found in urls.txt — add some and try again.", "WARN")
        log(f"File location: {URLS_FILE}", "INFO")
        return

    total = len(urls)
    log(f"Found {total} URL(s) to process", "INFO")

    # For video mode, ask resolution once for the whole batch
    batch_res = None
    if cfg.get("mode", "video") == "video":
        print(f"\n  {C.BOLD}Pick resolution for this batch (or Enter for default):{C.RESET}")
        for i, (val, label) in enumerate(RESOLUTION_OPTIONS, 1):
            marker = f" {C.GREEN}<- default{C.RESET}" if val == cfg.get("resolution", "best") else ""
            print(f"  {C.GREEN}[{i:>2}]{C.RESET} {label}{marker}")
        print(f"  {C.DIM}[Enter] Use default{C.RESET}")
        print()
        try:
            rc = input(f"  {C.CYAN}#{C.RESET} ").strip()
            if rc:
                idx = int(rc) - 1
                if 0 <= idx < len(RESOLUTION_OPTIONS):
                    batch_res = RESOLUTION_OPTIONS[idx][0]
                    log(f"Batch resolution: {RESOLUTION_OPTIONS[idx][1]}", "OK")
        except (ValueError, EOFError):
            pass

    print(f"{C.DIM}{'─' * 55}{C.RESET}")

    _stop_hint()
    results = []
    stopped = False
    for i, url in enumerate(urls, 1):
        try:
            result = download_single(url, cfg, i, total, resolution_override=batch_res)
        except DownloadStopped:
            log("Downloads stopped by user — remaining URLs kept in urls.txt.", "WARN")
            stopped = True
            break
        results.append(result)
        print(f"{C.DIM}{'─' * 55}{C.RESET}")

    ok_count = sum(1 for _, ok, _ in results if ok)
    fail_count = total - ok_count

    print(f"\n{C.BOLD}{'═' * 55}")
    print(f"  SUMMARY: {C.GREEN}{ok_count} succeeded{C.RESET}{C.BOLD}, {C.RED}{fail_count} failed{C.RESET}{C.BOLD} / {total} total")
    print(f"{'═' * 55}{C.RESET}\n")

    if fail_count > 0 or stopped:
        if fail_count > 0:
            log("Failed URLs:", "ERROR")
            for url, ok, msg in results:
                if not ok:
                    log(f"  {url} — {msg}", "ERROR")
    else:
        clear_urls()
        log("All downloads succeeded. urls.txt cleared.", "OK")

def process_single(cfg):
    """Prompt for a single URL, ask resolution if video, then download."""
    print()
    url = input(f"  {C.CYAN}Paste URL:{C.RESET} ").strip()
    if not url:
        log("No URL entered.", "WARN")
        return

    # Resolution prompt for video mode
    res_override = None
    if cfg.get("mode", "video") == "video":
        res_override = prompt_resolution(url)

    print(f"{C.DIM}{'─' * 55}{C.RESET}")
    _stop_hint()
    try:
        download_single(url, cfg, 1, 1, resolution_override=res_override)
    except DownloadStopped:
        log("Download stopped by user.", "WARN")
    print(f"{C.DIM}{'─' * 55}{C.RESET}")

# ── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    if sys.platform == "win32":
        os.system("")  # enable ANSI colors in cmd

    init_logging()
    banner()
    log(f"App directory: {APP_DIR}", "INFO")
    log(f"Log file: {LOG_FILE}", "INFO")

    cfg = load_config()

    # Ensure output dir exists
    Path(cfg.get("output_dir", OUTPUT_DIR)).mkdir(parents=True, exist_ok=True)

    save_config(cfg)

    if not URLS_FILE.exists():
        read_urls()
        log(f"Created urls.txt at {URLS_FILE}", "INFO")

    # Tool check on startup (throttled to every UPDATE_INTERVAL_DAYS)
    log("Checking tools...", "HEADER")
    if not run_updates(cfg):
        log("Some tools could not be downloaded. Downloads may fail.", "ERROR")
    print()

    # Quick cold-start checkup — fast; full probe only if login cache missing
    run_checkup(cfg, quick=True)
    print()

    # Main loop
    while True:
        try:
            show_menu(cfg)
            choice = input(f"  {C.CYAN}>{C.RESET} ").strip()

            if choice == "1":
                process_batch(cfg)
            elif choice == "2":
                process_single(cfg)
            elif choice == "3":
                choose_format(cfg)
            elif choice == "4":
                choose_resolution(cfg)
            elif choice == "5":
                change_output_folder(cfg)
            elif choice == "6":
                cfg["auto_update"] = not cfg.get("auto_update", True)
                save_config(cfg)
                log(f"Auto-update {'enabled' if cfg['auto_update'] else 'disabled'}", "OK")
            elif choice == "7":
                log("Forcing tool update...", "UPDATE")
                run_updates(cfg, force=True)
            elif choice == "8":
                out = cfg.get("output_dir", OUTPUT_DIR)
                open_path(out)
            elif choice == "9":
                open_path(URLS_FILE)
            elif choice == "10":
                run_checkup(cfg)
            elif choice == "11":
                choose_cookie_browser(cfg)
            elif choice == "12":
                if COOKIES_FILE.exists():
                    try:
                        COOKIES_FILE.unlink()
                        log("Saved login cookies deleted. They will be re-exported from your browser when next needed.", "OK")
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
        except Exception as e:
            log(f"Unexpected error: {e}", "ERROR")
            log(traceback.format_exc(), "ERROR")

    print()

if __name__ == "__main__":
    main()
