"""Detecting carousels and naming what comes out of them."""

import json
import re
from collections import namedtuple

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


#: What the probe learned about a post before anything is downloaded.
PostInfo = namedtuple("PostInfo", "count caption has_video address")

#: Platforms whose "address" is an @handle rather than a display name. A
#: YouTube channel is a name you read; an Instagram username is a handle you
#: type, and the @ is part of how people recognise it.
HANDLE_SITE_RE = re.compile(
    r"(instagram\.com|tiktok\.com"
    r"|(?<![A-Za-z0-9-])(twitter|x)\.com|threads\.(net|com))",
    re.IGNORECASE,
)

#: Filenames are "[Address] - [Caption]". Both halves are capped: 20 characters
#: is enough to recognise an account without a long channel name crowding out
#: the caption, and five words is enough to tell two posts apart while leaving
#: room for the carousel item number and the extension inside the platform's
#: path limit.
MAX_ADDRESS_CHARS = 20
MAX_CAPTION_WORDS = 5

#: Extensions gallery-dl reports for the items it would fetch. Anything not
#: recognised leaves has_video undecided rather than guessing "image".
VIDEO_EXTS = {"mp4", "mov", "webm", "mkv", "avi", "m4v", "flv", "ts", "3gp"}
IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "gif", "heic", "avif", "bmp"}


def _has_video(files):
    """True/False if the post's items are known, None if they are not.

    The distinction matters: a post that is certainly images-only can skip
    yt-dlp entirely, but an *unknown* post must still be offered to yt-dlp or a
    plain video would be downloaded by the wrong tool.
    """
    seen_any = False
    for entry in files:
        meta = entry[2] if len(entry) > 2 and isinstance(entry[2], dict) else {}
        if meta.get("video_url"):
            return True
        ext = str(meta.get("extension", "")).lower()
        if ext in VIDEO_EXTS:
            return True
        if ext in IMAGE_EXTS:
            seen_any = True
    return False if seen_any else None


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


def clean_address(address, url=None):
    """The account a post came from, trimmed to fit inside a filename.

    Returns None when there is nothing usable, so the caller can fall back
    rather than building a name with an empty half.
    """
    if not address:
        return None
    text = str(address).strip()
    # Stripped here and re-added below, so a source that already includes
    # the @ and one that does not both end up with exactly one.
    text = re.sub(r"^@+", "", text)
    # Braces go too, not only the Windows-illegal set: this name is handed
    # to gallery-dl as a format string ({num}, {extension}), so a brace in
    # a channel's display name would be read as a field instead of written.
    text = re.sub(r'[<>:"/\|?*{}]', "", text)
    text = re.sub(r"\s+", " ", text).strip(" .-")
    text = text[:MAX_ADDRESS_CHARS].strip(" .-")
    if not text:
        return None
    if url and HANDLE_SITE_RE.search(url):
        return "@" + text
    return text


def post_id(url):
    """Just the identifying part of a link — no site prefix.

    Used when a post has an account but no readable caption: "@someone -
    DblkhUwAYDz" still says who and which post, where the bare site-prefixed
    fallback would repeat the platform that the account name already implies.
    """
    for pattern, _ in _NAME_PATTERNS:
        m = re.search(pattern, url, re.IGNORECASE)
        if m:
            return re.sub(r'[<>:"/\|?*]', "_", m.groups()[-1])
    return None


def build_name(url, address, caption, max_words=MAX_CAPTION_WORDS):
    """The one place a downloaded file's name is decided.

    "[Address] - [Caption]", with either half dropped when it is unavailable
    rather than left as an empty bracket. Carousel items get " - 01" appended
    downstream, so this returns the shared stem for a whole post.
    """
    addr = clean_address(address, url)
    cap = clean_caption_words(caption, max_words) if caption else None

    if addr and cap:
        return f"{addr} - {cap}"
    if addr:
        ident = post_id(url)
        return f"{addr} - {ident}" if ident else addr
    if cap:
        return cap
    return post_folder_name(url)


#: Keys that carry the account name, in the order they are trusted. gallery-dl
#: names this field differently per site, and some nest it under an author
#: object, so both shapes are searched.
_ADDRESS_KEYS = ("username", "user_name", "uploader", "author_name",
                 "channel", "owner", "user", "author", "screen_name",
                 "nickname", "account")


def scan_address(obj, _depth=0):
    """Find the account name in gallery-dl's metadata, whatever it calls it."""
    if _depth > 6:
        return None
    if isinstance(obj, dict):
        for key in _ADDRESS_KEYS:
            v = obj.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
            # "author": {"name": ...} / {"username": ...}
            if isinstance(v, dict):
                for inner in ("username", "name", "nick", "screen_name"):
                    iv = v.get(inner)
                    if isinstance(iv, str) and iv.strip():
                        return iv.strip()
        for v in obj.values():
            r = scan_address(v, _depth + 1)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = scan_address(v, _depth + 1)
            if r:
                return r
    return None


def tiktok_video_id(url):
    m = re.search(r"/video/(\d+)", url) or re.search(r"item_id=(\d+)", url)
    return m.group(1) if m else None


def probe_post(url, cfg):
    """Fetch post metadata only (no media).

    Returns ``PostInfo(count, caption, has_video, address)``. ``has_video`` is
    None when
    the probe could not tell — the caller must treat that as "maybe", not "no",
    or a video post would be handed to the wrong downloader.
    """
    if not gallerydl_available():
        return PostInfo(None, None, None, None)
    rc, out = run_quiet(gallerydl_command() + cookie_args(cfg) + ["-j", url],
                        timeout=90)
    if not out:
        return PostInfo(None, None, None, None)

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
        return PostInfo(None, None, None, None)

    # Entries are [msg_type, ...]; msg_type 3 = one downloadable file.
    files = [e for e in data if isinstance(e, list) and e and e[0] == 3]
    count = len(files)

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

    return PostInfo(count if count > 0 else None, scan(data),
                    _has_video(files), scan_address(data))


def post_base_name(url, caption, cfg, address=None):
    """Human-friendly base name: "[Address] - [Caption]".

    ``folder_name_words`` still caps the caption so an existing config keeps
    working, but it now defaults to five words rather than four.
    """
    words = int(cfg.get("folder_name_words", MAX_CAPTION_WORDS))
    return build_name(url, address, caption, max_words=words)
