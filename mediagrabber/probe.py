"""Detecting carousels and naming what comes out of them."""

import json
import re

from .cookies import cookie_args
from .shell import run_quiet
from .tools import gallerydl_available, gallerydl_command

# Posts that may hold *several* items behind one URL. This drives yt-dlp's
# --yes-playlist and the "-f best" selector, so it stays narrow on purpose:
# widening it would strip --recode-video and the resolution preference from
# ordinary video posts on other sites.
CAROUSEL_URL_RE = re.compile(
    r"(instagram\.com/(p|reel)/|tiktok\.com/.+/photo/|vt\.tiktok\.com/)",
    re.IGNORECASE,
)

# Platforms whose posts we probe for item count and name from the caption.
# Every pattern is anchored to a *post-shaped* path — never a profile, board
# or subreddit root, because a bare profile URL plus --yes-playlist is how one
# link turns into a mass download.
POST_SITE_RE = re.compile(
    r"("
    r"instagram\.com/(p|reel|tv)/[A-Za-z0-9_-]+"
    r"|tiktok\.com/@[^/]+/(video|photo)/\d+"
    r"|vt\.tiktok\.com/[A-Za-z0-9]+"
    r"|(?<![A-Za-z0-9-])(twitter|x)\.com/[^/]+/status/\d+"
    r"|reddit\.com/r/[^/]+/comments/[A-Za-z0-9]+"
    r"|redd\.it/[A-Za-z0-9]+"
    r"|pinterest\.[a-z.]+/pin/\d+"
    r"|pin\.it/[A-Za-z0-9]+"
    r"|threads\.(net|com)/@[^/]+/post/[A-Za-z0-9_-]+"
    r")",
    re.IGNORECASE,
)

# Sites that commonly refuse media without an authenticated session. Keep this
# in step with cookies.LOGIN_DOMAINS — a site listed here but missing there
# gets the login code path with no actual cookie behind it.
LOGIN_SITE_RE = re.compile(
    r"(instagram\.com|tiktok\.com"
    r"|(?<![A-Za-z0-9-])(twitter|x)\.com|threads\.(net|com))",
    re.IGNORECASE,
)


def needs_login(url):
    """Sites where downloads commonly require an authenticated session."""
    return bool(LOGIN_SITE_RE.search(url))


def is_carousel_candidate(url):
    """May this one URL yield several files? Gates --yes-playlist / -f best."""
    return bool(CAROUSEL_URL_RE.search(url))


def is_post_url(url):
    """A post on a platform we probe and name properly (not just any link)."""
    return bool(POST_SITE_RE.search(url))


#: Link-shape -> name, tried in order. Each yields a stable, readable name for
#: a post whose caption could not be read (private, rate-limited, no gallery-dl).
_NAME_PATTERNS = [
    (r"instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)", "instagram_{0}"),
    (r"tiktok\.com/@([^/]+)/(?:photo|video)/(\d+)", "tiktok_{0}_{1}"),
    (r"vt\.tiktok\.com/([A-Za-z0-9]+)", "tiktok_{0}"),
    (r"(?:twitter|x)\.com/([^/]+)/status/(\d+)", "twitter_{0}_{1}"),
    (r"reddit\.com/r/([^/]+)/comments/([A-Za-z0-9]+)", "reddit_{0}_{1}"),
    (r"redd\.it/([A-Za-z0-9]+)", "reddit_{0}"),
    (r"pinterest\.[a-z.]+/pin/(\d+)", "pinterest_{0}"),
    (r"pin\.it/([A-Za-z0-9]+)", "pinterest_{0}"),
    (r"threads\.(?:net|com)/@([^/]+)/post/([A-Za-z0-9_-]+)", "threads_{0}_{1}"),
]


def post_folder_name(url):
    """Fallback name derived from the link itself when there is no caption."""
    for pattern, template in _NAME_PATTERNS:
        m = re.search(pattern, url, re.IGNORECASE)
        if m:
            name = template.format(*m.groups())
            return re.sub(r'[<>:"/\|?*]', "_", name)[:80]
    seg = re.sub(r"[?#].*$", "", url).rstrip("/").rsplit("/", 1)[-1]
    seg = re.sub(r'[<>:"/\|?*]', "_", seg)[:80]
    return seg or "post"


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
    return post_folder_name(url)
