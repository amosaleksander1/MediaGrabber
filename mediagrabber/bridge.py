"""Native-messaging bridge: the browser extension talks to MediaGrabber here.

The browser spawns this as a child process and speaks over stdin/stdout. Every
message is a 4-byte length prefix in *native* byte order followed by UTF-8 JSON.
Chrome refuses inbound messages over 1 MB, so the extension sends cookies in
batches rather than one payload.

Why this exists: reading a browser's cookie jar from the outside means defeating
its encryption — App-Bound Encryption on Windows, the Keychain on macOS — which
is what most of cookies.py does. An extension already holds the cookies and can
simply hand them over, so this path needs no decryption, no headless relaunch
and no "please close your browser" step.

Nothing here is a downloader: the extension sends cookies and URLs, and the
existing engine does the work.

Run by hand to check the framing without a browser:

    printf '...' | python bridge_main.py        # see tests/test_bridge.py
"""

import json
import struct
import sys
import traceback
from pathlib import Path

from . import APP_VERSION
from .config import COOKIES_FILE, TOOLS_DIR, URLS_FILE
from .cookies import LOGIN_DOMAINS, cdp_cookies_to_netscape

#: Chrome drops anything larger; guard so a malformed length cannot make us
#: allocate wildly.
MAX_MESSAGE_BYTES = 1024 * 1024

#: Only these reach yt-dlp/gallery-dl. A URL starting with "-" would be read as
#: an argument rather than a link, so the scheme check is a safety boundary,
#: not a formality.
ALLOWED_SCHEMES = ("http://", "https://")


# ── WIRE FORMAT ──────────────────────────────────────────────────────────────

def read_message(stream=None):
    """Read one length-prefixed JSON message. Returns None at end of stream."""
    stream = stream if stream is not None else sys.stdin.buffer
    header = stream.read(4)
    if not header or len(header) < 4:
        return None
    # "@I" is native byte order and size, which is what the browsers write.
    (length,) = struct.unpack("@I", header)
    if length == 0 or length > MAX_MESSAGE_BYTES:
        raise ValueError(f"refusing a {length}-byte message")
    body = stream.read(length)
    if len(body) < length:
        return None
    return json.loads(body.decode("utf-8"))


def write_message(payload, stream=None):
    """Write one length-prefixed JSON message."""
    stream = stream if stream is not None else sys.stdout.buffer
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    stream.write(struct.pack("@I", len(body)))
    stream.write(body)
    stream.flush()


# ── COOKIES ──────────────────────────────────────────────────────────────────

def extension_cookies_to_cdp(cookies):
    """Map the ``chrome.cookies`` shape onto the one cookies.py already writes.

    The two are nearly identical; the difference that matters is the expiry
    field name, and that a session cookie has no expiry at all.
    """
    out = []
    for c in cookies or []:
        if not isinstance(c, dict) or not c.get("domain"):
            continue
        expires = c.get("expirationDate", c.get("expires", 0)) or 0
        out.append({
            "domain": c.get("domain", ""),
            "path": c.get("path", "/") or "/",
            "secure": bool(c.get("secure")),
            "expires": int(float(expires)),
            "httpOnly": bool(c.get("httpOnly")),
            "name": c.get("name", ""),
            "value": c.get("value", ""),
        })
    return out


def wanted_cookie(cookie):
    """True if the cookie belongs to a site the app actually logs into."""
    domain = (cookie.get("domain") or "").lstrip(".").lower()
    return any(domain == d or domain.endswith("." + d) for d in LOGIN_DOMAINS)


# ── URL SAFETY ───────────────────────────────────────────────────────────────

def safe_url(url):
    """Return a usable http(s) URL, or None.

    Anything else — a ``file://`` path, a ``javascript:`` URI, or a string
    starting with "-" that the downloaders would parse as a flag — is refused.
    """
    if not isinstance(url, str):
        return None
    url = url.strip()
    if not url or len(url) > 2048:
        return None
    return url if url.lower().startswith(ALLOWED_SCHEMES) else None


# ── HANDLERS ─────────────────────────────────────────────────────────────────

def handle_hello(_msg):
    """Identify the app so the extension can show connected state and version."""
    return {
        "ok": True,
        "app": "MediaGrabber",
        "version": APP_VERSION,
        "domains": list(LOGIN_DOMAINS),
        "capabilities": ["cookies", "queue"],
    }


def handle_cookies(msg):
    """Write the cookies the extension collected to tools/cookies.txt.

    ``append`` is set on every batch after the first, so a refresh replaces the
    old session rather than growing the file forever.
    """
    incoming = msg.get("cookies") or []
    append = bool(msg.get("append"))
    mapped = [c for c in extension_cookies_to_cdp(incoming) if wanted_cookie(c)]
    if not mapped:
        return {"ok": False, "error": "no cookies for any site MediaGrabber logs into",
                "written": 0}
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    written = cdp_cookies_to_netscape(mapped, COOKIES_FILE, append=append)
    domains = sorted({c["domain"].lstrip(".").lower() for c in mapped})
    return {"ok": written > 0, "written": written, "domains": domains,
            "path": str(COOKIES_FILE)}


def handle_queue(msg):
    """Append a URL to urls.txt for the next run.

    Phase A deliberately stops here: the extension can hand over a link, and the
    app downloads it on its next batch. Downloading straight from the bridge is
    Phase B, where a browser being closed must not kill an in-flight download.
    """
    url = safe_url(msg.get("url"))
    if not url:
        return {"ok": False, "error": "not an http(s) URL"}

    existing = ""
    if URLS_FILE.exists():
        existing = URLS_FILE.read_text(encoding="utf-8", errors="replace")
    if url in existing:
        return {"ok": True, "queued": False, "reason": "already queued", "url": url}

    body = existing if existing.endswith("\n") or not existing else existing + "\n"
    URLS_FILE.parent.mkdir(parents=True, exist_ok=True)
    URLS_FILE.write_text(body + url + "\n", encoding="utf-8")
    return {"ok": True, "queued": True, "url": url}


HANDLERS = {
    "hello": handle_hello,
    "cookies": handle_cookies,
    "queue": handle_queue,
}


def dispatch(msg):
    """Route one message to its handler, echoing the request id back."""
    if not isinstance(msg, dict):
        return {"ok": False, "error": "message must be an object"}
    kind = msg.get("type")
    handler = HANDLERS.get(kind)
    if handler is None:
        return {"ok": False, "error": f"unknown message type: {kind!r}"}
    try:
        reply = handler(msg)
    except Exception as e:  # a handler fault must not kill the connection
        reply = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if msg.get("id") is not None:
        reply["id"] = msg["id"]
    reply.setdefault("type", kind)
    return reply


def serve(stdin=None, stdout=None):
    """Message loop. Returns when the browser closes the port."""
    while True:
        try:
            msg = read_message(stdin)
        except Exception as e:
            write_message({"ok": False, "error": f"unreadable message: {e}"}, stdout)
            return 1
        if msg is None:
            return 0
        write_message(dispatch(msg), stdout)


def main(argv=None):
    """Entry point. argv differs per browser and is deliberately ignored.

    Chrome passes the extension origin (plus --parent-window on Windows) and
    Firefox passes the manifest path and extension ID. Neither is needed: the
    manifest's allow-list is what restricts who may connect, and it is enforced
    by the browser before this process starts.
    """
    try:
        return serve()
    except Exception:
        # stdout belongs to the protocol, so a crash report goes beside the app.
        try:
            log_path = Path(TOOLS_DIR) / "bridge-error.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                traceback.print_exc(file=f)
        except Exception:
            pass
        return 1
