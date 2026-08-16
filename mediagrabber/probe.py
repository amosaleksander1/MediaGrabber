"""Detecting carousels and naming what comes out of them."""

import json
import re

from .cookies import cookie_args
from .shell import run_quiet
from .tools import gallerydl_available, gallerydl_command

# Instagram posts (may be carousels) and TikTok photo-mode posts.
CAROUSEL_URL_RE = re.compile(
    r"(instagram\.com/(p|reel)/|tiktok\.com/.+/photo/|vt\.tiktok\.com/)",
    re.IGNORECASE,
)

LOGIN_SITE_RE = re.compile(r"(instagram\.com|tiktok\.com)", re.IGNORECASE)


def needs_login(url):
    """Sites where downloads commonly require an authenticated session."""
    return bool(LOGIN_SITE_RE.search(url))


def is_carousel_candidate(url):
    return bool(CAROUSEL_URL_RE.search(url))


def carousel_folder_name(url):
    """Fallback folder name derived from the link itself."""
    m = re.search(r"instagram\.com/(?:p|reel)/([A-Za-z0-9_-]+)", url, re.IGNORECASE)
    if m:
        return f"instagram_{m.group(1)}"
    m = re.search(r"tiktok\.com/@([^/]+)/photo/(\d+)", url, re.IGNORECASE)
    if m:
        return f"tiktok_{m.group(1)}_{m.group(2)}"
    m = re.search(r"vt\.tiktok\.com/([A-Za-z0-9]+)", url, re.IGNORECASE)
    if m:
        return f"tiktok_{m.group(1)}"
    seg = re.sub(r"[?#].*$", "", url).rstrip("/").rsplit("/", 1)[-1]
    seg = re.sub(r'[<>:"/\\|?*]', "_", seg)[:80]
    return seg or "carousel"


def clean_caption_words(caption, max_words):
    """First few caption words as a filesystem-safe name.

    Colons and slashes are stripped for Windows; the leading-dot strip also
    keeps macOS/Linux from producing a hidden folder.
    """
    text = re.sub(r"https?://\S+", " ", caption)
    text = re.sub(r"[#@]\S+", " ", text)        # hashtags & mentions
    text = re.sub(r"[^\w\s'\-]", " ", text)     # emojis & punctuation
    words = [w for w in text.split() if w]
    if not words:
        return None
    name = " ".join(words[:max_words])
    name = re.sub(r'[<>:"/\\|?*{}]', "", name).strip(" .-")
    return name[:60] or None


def tiktok_video_id(url):
    m = re.search(r"/video/(\d+)", url) or re.search(r"item_id=(\d+)", url)
    return m.group(1) if m else None


def probe_post(url, cfg):
    """Fetch post metadata only (no media). Returns (media_count, caption)."""
    if not gallerydl_available():
        return (None, None)
    rc, out = run_quiet(gallerydl_command() + cookie_args(cfg) + ["-j", url],
                        timeout=90)
    if not out:
        return (None, None)

    # gallery-dl -j prints a pretty JSON array, possibly preceded by warning
    # lines. Try decoding from each "[" until one parses.
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

    # Entries are [msg_type, ...]; msg_type 3 = one downloadable file.
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
    """Human-friendly base name: caption words, else a link-derived name."""
    if caption:
        name = clean_caption_words(caption, int(cfg.get("folder_name_words", 4)))
        if name:
            return name
    return carousel_folder_name(url)
