"""Paths, defaults and config persistence."""

import json
from pathlib import Path

from .platform_support import EXE, app_dir

# ── PATHS ────────────────────────────────────────────────────────────────────

APP_DIR = app_dir()
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
COOKIES_FILE = TOOLS_DIR / "cookies.txt"

OUTPUT_DIR = str(Path.home() / "Downloads" / "MediaGrabber")

# ── BEHAVIOUR ────────────────────────────────────────────────────────────────

#: Only check bundled tools for updates this often (or when one breaks).
UPDATE_INTERVAL_DAYS = 14

#: Real-browser User-Agent. TikTok rejects yt-dlp's default UA with
#: "Unexpected response from webpage request" since Aug 2026 (yt-dlp #17403).
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

#: Errors that should never be retried.
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

#: Errors that usually mean the downloader itself is outdated — these trigger
#: a forced tool update plus one free retry.
TOOL_FAILURE_MARKERS = [
    "unable to extract",
    "js challenge",
    "failed to solve",
    "unsupported url",
    "please report this issue",
    "confirm you are on the latest version",
    "http error 403",
]

# ── FORMAT LISTS ─────────────────────────────────────────────────────────────

VIDEO_FORMATS = [
    ("mp4", "MP4  — Universal, plays everywhere"),
    ("mkv", "MKV  — High quality, flexible containers"),
    ("webm", "WebM — Web-optimized, smaller size"),
    ("avi", "AVI  — Legacy, wide compatibility"),
    ("mov", "MOV  — Apple/QuickTime native"),
    ("flv", "FLV  — Flash Video (legacy)"),
    ("ts", "TS   — Transport Stream (broadcast)"),
]

AUDIO_FORMATS = [
    ("mp3", "MP3  — Universal audio, good compression"),
    ("aac", "AAC  — Better quality than MP3 at same bitrate"),
    ("flac", "FLAC — Lossless, large files, perfect quality"),
    ("wav", "WAV  — Uncompressed, studio quality"),
    ("opus", "Opus — Modern, best compression-to-quality"),
    ("ogg", "OGG  — Open format, good quality"),
    ("m4a", "M4A  — Apple/iTunes standard"),
    ("alac", "ALAC — Apple Lossless"),
    ("aiff", "AIFF — Uncompressed, Apple-native"),
    ("wma", "WMA  — Windows Media Audio"),
]

RESOLUTION_OPTIONS = [
    ("best", "Best available quality"),
    ("2160", "4K   (2160p)"),
    ("1440", "QHD  (1440p)"),
    ("1080", "FHD  (1080p)"),
    ("720", "HD   (720p)"),
    ("480", "SD   (480p)"),
    ("360", "Low  (360p)"),
    ("worst", "Worst (smallest file)"),
]

DEFAULTS = {
    "mode": "video",
    "video_format": "mp4",
    "audio_format": "mp3",
    "resolution": "best",
    "output_dir": OUTPUT_DIR,
    "filename_template": "%(title)s.%(ext)s",
    "auto_update": True,
    "max_retries": 3,
    # Browser to borrow login cookies from. "auto" detects, "none" disables.
    "cookies_browser": "auto",
    # Carousel folders are named from the first N words of the post caption.
    "folder_name_words": 4,
}


def load_config():
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    return cfg


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def load_versions():
    if VERSION_FILE.exists():
        try:
            with open(VERSION_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_versions(versions):
    VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        json.dump(versions, f, indent=2)
