#!/usr/bin/env python3
"""Native-messaging bridge: wire format, cookie mapping, URL safety, manifests.

None of this needs a browser, which is the point — the framing is the part most
likely to break silently, and a framing bug looks like "the extension does
nothing" rather than an error.

Run:  python3 tests/test_bridge.py
"""

import io
import json
import os
import struct
import subprocess
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover — exotic stream
        pass

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from mediagrabber.bridge import (dispatch, extension_cookies_to_cdp,  # noqa: E402
                                 read_message, safe_url, wanted_cookie,
                                 write_message)

# A cookie exactly as chrome.cookies.getAll() hands it over.
CHROME_COOKIE = {
    "domain": ".instagram.com",
    "expirationDate": 1799999999.7,
    "httpOnly": True,
    "name": "sessionid",
    "path": "/",
    "secure": True,
    "value": "abc123",
    "sameSite": "no_restriction",
}


def check_framing(fail):
    """4-byte native-order length prefix, then UTF-8 JSON."""
    buf = io.BytesIO()
    write_message({"type": "hello", "id": 7}, buf)
    raw = buf.getvalue()

    (length,) = struct.unpack("@I", raw[:4])
    if length != len(raw) - 4:
        fail(f"length prefix {length} does not match body {len(raw) - 4}")

    buf.seek(0)
    back = read_message(buf)
    if back != {"type": "hello", "id": 7}:
        fail(f"round trip changed the message: {back!r}")

    # A closed stream must read as "no more messages", not raise.
    if read_message(io.BytesIO(b"")) is not None:
        fail("empty stream should read as None")
    # A truncated body is also end-of-stream, not a crash.
    if read_message(io.BytesIO(struct.pack("@I", 50) + b"{}")) is not None:
        fail("truncated body should read as None")

    # An absurd length must be refused rather than allocated.
    try:
        read_message(io.BytesIO(struct.pack("@I", 99_000_000) + b"{}"))
        fail("oversized message was not refused")
    except ValueError:
        pass


def check_cookies(fail):
    mapped = extension_cookies_to_cdp([CHROME_COOKIE])
    if len(mapped) != 1:
        fail("chrome cookie did not map")
        return
    c = mapped[0]
    if c["expires"] != 1799999999:
        fail(f"expirationDate not carried across: {c['expires']}")
    for key in ("domain", "path", "secure", "httpOnly", "name", "value"):
        if key not in c:
            fail(f"mapped cookie missing {key}")
    if not c["httpOnly"]:
        fail("httpOnly lost — that flag is why the extension exists")

    # A session cookie has no expirationDate at all.
    session = dict(CHROME_COOKIE)
    del session["expirationDate"]
    if extension_cookies_to_cdp([session])[0]["expires"] != 0:
        fail("session cookie should map to expires 0")

    # Junk must not crash the mapper.
    if extension_cookies_to_cdp([None, {}, {"name": "x"}]) != []:
        fail("cookies without a domain should be dropped")

    if not wanted_cookie({"domain": ".instagram.com"}):
        fail("instagram cookie should be wanted")
    if not wanted_cookie({"domain": "www.reddit.com"}):
        fail("subdomain of a login site should be wanted")
    if wanted_cookie({"domain": "ads.example.com"}):
        fail("unrelated domain must not be collected")
    # "notx.com" ends with "x.com" as a string but is a different site.
    if wanted_cookie({"domain": "notx.com"}):
        fail("suffix lookalike must not match")


def check_url_safety(fail):
    for good in ("https://www.instagram.com/p/Cabc/",
                 "http://example.com/a.jpg"):
        if safe_url(good) != good:
            fail(f"rejected a valid URL: {good}")
    # A leading "-" would be read as a flag by yt-dlp, not as a link.
    for bad in ("--config-location=/etc/passwd", "-x", "file:///etc/passwd",
                "javascript:alert(1)", "", "   ", None, 42, "x" * 3000):
        if safe_url(bad) is not None:
            fail(f"accepted an unsafe URL: {bad!r}")


def check_dispatch(fail):
    reply = dispatch({"type": "hello", "id": 1})
    if not reply.get("ok") or reply.get("id") != 1:
        fail(f"hello did not answer correctly: {reply!r}")
    if "version" not in reply:
        fail("hello should report the app version")

    unknown = dispatch({"type": "nope"})
    if unknown.get("ok"):
        fail("unknown message type should not report ok")

    if dispatch("not a dict").get("ok"):
        fail("non-object message should not report ok")

    # A handler that throws must answer, not kill the connection.
    bad = dispatch({"type": "cookies", "cookies": "not-a-list"})
    if bad.get("ok"):
        fail("malformed cookies payload should not report ok")


def check_manifests(fail):
    """Manifest shape per browser, resolved for every OS from this one host."""
    child = (
        "import sys, json, types\n"
        "sys.platform = {plat!r}\n"
        "sys.path.insert(0, {repo!r})\n"
        "import pathlib\n"
        "pathlib.Path.home = classmethod(lambda cls: pathlib.Path({home!r}))\n"
        "from mediagrabber import nativehost as nh\n"
        "out = {{}}\n"
        "for b in ('chrome', 'firefox', 'brave', 'safari'):\n"
        "    d = nh.manifest_dir(b)\n"
        "    out[b] = {{'dir': str(d) if d else None,\n"
        "              'key': nh._win_registry_key(b),\n"
        "              'doc': nh.manifest_content(b, '/tmp/bridge') if d else None}}\n"
        "print(json.dumps(out))\n"
    )
    for plat, home, marker in (
        # On Windows the manifest can live anywhere; a registry value points at
        # it, so the app keeps its copies together under tools/.
        ("win32", "C:\\\\Users\\\\test", "native-hosts"),
        ("darwin", "/Users/test", "Library/Application Support"),
        ("linux", "/home/test", "NativeMessagingHosts"),
    ):
        code = child.format(plat=plat, repo=REPO, home=home)
        r = subprocess.run([sys.executable, "-c", code],
                           capture_output=True, text=True)
        if r.returncode != 0:
            fail(f"{plat}: manifest child failed: {r.stderr.strip()[:300]}")
            continue
        got = json.loads(r.stdout.strip().splitlines()[-1])

        if got["safari"]["dir"] is not None:
            fail(f"{plat}: Safari cannot use native messaging and must be skipped")

        chrome = got["chrome"]["doc"]
        if "allowed_origins" not in chrome:
            fail(f"{plat}: Chrome manifest needs allowed_origins")
        elif not chrome["allowed_origins"][0].startswith("chrome-extension://"):
            fail(f"{plat}: Chrome origin malformed: {chrome['allowed_origins']}")
        if chrome.get("type") != "stdio":
            fail(f"{plat}: manifest type must be stdio")

        firefox = got["firefox"]["doc"]
        if "allowed_extensions" not in firefox:
            fail(f"{plat}: Firefox manifest needs allowed_extensions, not origins")
        if "allowed_origins" in firefox:
            fail(f"{plat}: Firefox must not carry allowed_origins")

        if marker not in got["chrome"]["dir"].replace("\\", "/"):
            fail(f"{plat}: unexpected manifest dir {got['chrome']['dir']}")

        # Each browser family reads its own registry hive on Windows; sharing
        # Chrome's key would leave Firefox and Brave silently unregistered.
        if plat == "win32":
            keys = {b: got[b]["key"] for b in ("chrome", "firefox", "brave")}
            if not all(keys.values()):
                fail(f"win32: missing registry key for {keys}")
            elif len(set(keys.values())) != 3:
                fail(f"win32: browsers must not share a registry key: {keys}")
            elif "Mozilla" not in (keys["firefox"] or ""):
                fail(f"win32: Firefox must use the Mozilla hive, got {keys['firefox']}")


def check_end_to_end(fail):
    """Pipe a real framed message through bridge_main.py, as a browser would."""
    payload = json.dumps({"type": "hello", "id": 99}).encode("utf-8")
    stdin = struct.pack("@I", len(payload)) + payload
    r = subprocess.run([sys.executable, os.path.join(REPO, "bridge_main.py")],
                       input=stdin, capture_output=True)
    if r.returncode != 0:
        fail(f"bridge exited {r.returncode}: {r.stderr.decode()[:300]}")
        return
    out = r.stdout
    if len(out) < 4:
        fail("bridge wrote no framed reply")
        return
    (length,) = struct.unpack("@I", out[:4])
    reply = json.loads(out[4:4 + length].decode("utf-8"))
    if not reply.get("ok") or reply.get("id") != 99:
        fail(f"bridge reply wrong: {reply!r}")


def main():
    failures = []
    fail = failures.append

    check_framing(fail)
    check_cookies(fail)
    check_url_safety(fail)
    check_dispatch(fail)
    check_manifests(fail)
    check_end_to_end(fail)

    print("Checked wire framing, cookie mapping, URL safety, dispatch, "
          "per-OS manifests and a real stdio round trip.")
    print("=" * 60)
    if failures:
        print("FAILURES:")
        for f in failures:
            print("  x " + f)
        return 1
    print("Bridge behaves correctly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
