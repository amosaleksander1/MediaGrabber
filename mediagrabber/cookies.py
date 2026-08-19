"""Login via borrowed browser session cookies.

Storing a username/password is a dead end: Instagram blocks scripted logins
and 2FA breaks them outright. Instead we borrow the session cookies from a
browser the user is already logged into, and cache them to a Netscape
cookies.txt that both yt-dlp and gallery-dl accept.

Per-platform cookie encryption is the interesting part:

* **Windows** — Chromium v127+ uses App-Bound Encryption, which only Chrome
  itself can unwrap (yt-dlp #10927). Worked around by driving headless Chrome
  over the DevTools Protocol and asking it to hand over its own cookies.
* **macOS** — Chromium encrypts with a Keychain item ("Chrome Safe Storage").
  yt-dlp reads it natively, but the first read pops a Keychain prompt. Safari
  is also available and needs Full Disk Access for the terminal.
* **Linux** — Chromium uses kwallet/gnome-keyring or a hardcoded key; yt-dlp
  handles all of it.

Firefox-family browsers (including Zen) store cookies in plain SQLite on every
platform, which is why they are preferred in :data:`BROWSER_ORDER`.
"""

import base64
import json
import os
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen

from .config import COOKIES_FILE, YTDLP_EXE, save_config
from .platform_support import IS_MAC, IS_WIN, NO_WINDOW
from .shell import run_quiet
from .ui import log, pick_from_list

# Firefox-family first: their cookie stores need no decryption dance.
# Safari is macOS-only and is appended by browser_order().
BROWSER_ORDER = ["zen", "firefox", "chrome", "edge", "brave", "vivaldi", "opera"]

CHROMIUM_BROWSERS = ("chrome", "edge", "brave", "vivaldi", "opera")

# Output markers meaning the browser is locking its cookie database.
COOKIE_LOCK_MARKERS = ["could not copy", "permission denied", "errno 13"]

# Sentinel: the browser is still running, so no debugging session can start.
CHROME_RUNNING = "browser_running"


def browser_order():
    return BROWSER_ORDER + (["safari"] if IS_MAC else [])


# ── PROFILE LOCATIONS ────────────────────────────────────────────────────────

def browser_profile_paths():
    if IS_WIN:
        local = os.environ.get("LOCALAPPDATA", "")
        roaming = os.environ.get("APPDATA", "")
        return {
            "zen": Path(roaming) / "zen" / "Profiles",
            "firefox": Path(roaming) / "Mozilla" / "Firefox" / "Profiles",
            "chrome": Path(local) / "Google" / "Chrome" / "User Data",
            "edge": Path(local) / "Microsoft" / "Edge" / "User Data",
            "brave": Path(local) / "BraveSoftware" / "Brave-Browser" / "User Data",
            "vivaldi": Path(local) / "Vivaldi" / "User Data",
            "opera": Path(roaming) / "Opera Software" / "Opera Stable",
        }

    home = Path.home()

    if IS_MAC:
        support = home / "Library" / "Application Support"
        return {
            "zen": support / "zen" / "Profiles",
            "firefox": support / "Firefox" / "Profiles",
            "chrome": support / "Google" / "Chrome",
            "edge": support / "Microsoft Edge",
            "brave": support / "BraveSoftware" / "Brave-Browser",
            "vivaldi": support / "Vivaldi",
            "opera": support / "com.operasoftware.Opera",
            # Safari's cookie jar; reading it requires Full Disk Access.
            "safari": home / "Library" / "Cookies",
        }

    return {
        "zen": home / ".zen",
        "firefox": home / ".mozilla" / "firefox",
        "chrome": home / ".config" / "google-chrome",
        "edge": home / ".config" / "microsoft-edge",
        "brave": home / ".config" / "BraveSoftware" / "Brave-Browser",
        "vivaldi": home / ".config" / "vivaldi",
        "opera": home / ".config" / "opera",
    }


def _zen_profile():
    """The active Zen profile — the one with the freshest cookie DB."""
    root = browser_profile_paths()["zen"]
    if not root.exists():
        return None
    cands = [p for p in root.iterdir()
             if p.is_dir() and (p / "cookies.sqlite").exists()]
    return max(cands, key=lambda p: (p / "cookies.sqlite").stat().st_mtime) if cands else None


def cookie_browser_spec(browser):
    """Translate our browser name into the spec yt-dlp/gallery-dl understand.

    Zen is Firefox-based but unknown to both tools, so its profile path is
    passed explicitly as ``firefox:<path>``.
    """
    if browser == "zen":
        prof = _zen_profile()
        return f"firefox:{prof}" if prof else None
    return browser


def detect_installed_browsers():
    paths = browser_profile_paths()
    return [b for b in browser_order() if paths.get(b) and paths[b].exists()]


def resolve_cookie_browser(cfg):
    b = cfg.get("cookies_browser", "auto")
    if b == "none":
        return None
    if b != "auto":
        return b
    found = detect_installed_browsers()
    return found[0] if found else None


# Domains worth pulling out of a browser profile. Only sites that gate media
# behind a login need a cookie: exporting everything would put unrelated
# session cookies in a file on disk for no benefit.
LOGIN_DOMAINS = (
    "instagram.com",
    "tiktok.com",
    "x.com",
    "twitter.com",
    "threads.net",
    "threads.com",
    "reddit.com",
)


def cookie_cache_valid():
    return COOKIES_FILE.exists() and COOKIES_FILE.stat().st_size > 0


def cookie_args(cfg):
    """Cookie flags for yt-dlp / gallery-dl (both accept the same syntax).

    The cached file is preferred: it keeps working while the browser is open
    and holding a lock on its own cookie database.
    """
    if cookie_cache_valid():
        return ["--cookies", str(COOKIES_FILE)]
    spec = cookie_browser_spec(resolve_cookie_browser(cfg))
    return ["--cookies-from-browser", spec] if spec else []


# ── CHROMIUM VIA DEVTOOLS PROTOCOL ───────────────────────────────────────────
# On Windows this is the only way in (App-Bound Encryption). On macOS it is
# optional but nicer: the browser decrypts its own Keychain-protected cookies,
# so the user never sees a "wants to access your keychain" prompt.

def _chromium_exe_names(browser):
    if IS_MAC:
        return {
            "chrome": ["Google Chrome"],
            "edge": ["Microsoft Edge"],
            "brave": ["Brave Browser"],
            "vivaldi": ["Vivaldi"],
            "opera": ["Opera"],
        }.get(browser, [])
    return {
        "chrome": ["chrome.exe"],
        "edge": ["msedge.exe"],
        "brave": ["brave.exe"],
        "vivaldi": ["vivaldi.exe"],
        "opera": ["opera.exe", "launcher.exe"],
    }.get(browser, [])


def find_chromium_binary(browser):
    """Locate a Chromium-family browser executable."""
    names = _chromium_exe_names(browser)
    if not names:
        return None

    if IS_MAC:
        for app in names:
            for root in (Path("/Applications"),
                         Path.home() / "Applications"):
                exe = root / f"{app}.app" / "Contents" / "MacOS" / app
                if exe.exists():
                    return str(exe)
        return None

    if not IS_WIN:
        return None

    # Windows: registry App Paths first, then the usual install roots.
    try:
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for exe in names:
                try:
                    key = winreg.OpenKey(
                        hive,
                        r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\\" + exe)
                    val, _ = winreg.QueryValueEx(key, None)
                    if val and Path(val).exists():
                        return val
                except OSError:
                    continue
    except Exception:
        pass

    roots = [os.environ.get("PROGRAMFILES", r"C:\Program Files"),
             os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
             os.environ.get("LOCALAPPDATA", "")]
    subdirs = {
        "chrome": [r"Google\Chrome\Application"],
        "edge": [r"Microsoft\Edge\Application"],
        "brave": [r"BraveSoftware\Brave-Browser\Application"],
        "vivaldi": [r"Vivaldi\Application"],
        "opera": [r"Opera", r"Programs\Opera"],
    }.get(browser, [])
    for root in roots:
        if not root:
            continue
        for sub in subdirs:
            for exe in names:
                p = Path(root) / sub / exe
                if p.exists():
                    return str(p)
    return None


class _MiniWS:
    """Minimal RFC6455 WebSocket client (text frames only) — stdlib only."""

    def __init__(self, ws_url, timeout=20):
        import socket
        from urllib.parse import urlparse
        u = urlparse(ws_url)
        host, port = u.hostname, u.port or 80
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        path = u.path + (("?" + u.query) if u.query else "")
        req = (f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\n"
               "Upgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise OSError("WebSocket handshake failed")
            buf += chunk
        if b" 101 " not in buf.split(b"\r\n", 1)[0]:
            raise OSError("WebSocket upgrade rejected")
        self._tail = buf.split(b"\r\n\r\n", 1)[1]

    def send(self, text):
        payload = text.encode()
        header = bytearray([0x81])  # FIN + text opcode
        mask = os.urandom(4)
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += n.to_bytes(2, "big")
        else:
            header.append(0x80 | 127)
            header += n.to_bytes(8, "big")
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def _recv_exact(self, n):
        data = self._tail
        self._tail = b""
        while len(data) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise OSError("WebSocket closed")
            data += chunk
        self._tail = data[n:]
        return data[:n]

    def recv(self):
        out = b""
        while True:
            b0, b1 = self._recv_exact(2)
            fin = b0 & 0x80
            length = b1 & 0x7F
            if length == 126:
                length = int.from_bytes(self._recv_exact(2), "big")
            elif length == 127:
                length = int.from_bytes(self._recv_exact(8), "big")
            out += self._recv_exact(length) if length else b""
            if fin:
                return out.decode("utf-8", "replace")

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


def _cdp_call(ws, req_id, method, params=None):
    msg = {"id": req_id, "method": method}
    if params:
        msg["params"] = params
    ws.send(json.dumps(msg))
    for _ in range(40):
        r = json.loads(ws.recv())
        if r.get("id") == req_id:
            return r.get("result", {}) or {}
    return {}


def cdp_cookies_to_netscape(cookies, path, append=False):
    """Write CDP ``Network.getAllCookies`` output as a Netscape cookies.txt.

    ``append`` adds rows to an existing file instead of replacing it. The
    browser extension sends cookies in batches, and because each batch arrives
    in its own native-host process nothing can be accumulated in memory — so
    only the first batch clears what was there before.
    """
    lines = [] if append else ["# Netscape HTTP Cookie File",
                               "# Exported by MediaGrabber", ""]
    n = 0
    for c in cookies:
        domain = c.get("domain", "")
        if not domain:
            continue
        flag = "TRUE" if domain.startswith(".") else "FALSE"
        cpath = c.get("path", "/") or "/"
        secure = "TRUE" if c.get("secure") else "FALSE"
        expires = max(int(c.get("expires", 0) or 0), 0)
        prefix = "#HttpOnly_" if c.get("httpOnly") else ""
        lines.append(f"{prefix}{domain}\t{flag}\t{cpath}\t{secure}\t{expires}\t"
                     f"{c.get('name', '')}\t{c.get('value', '')}")
        n += 1
    text = ("\n".join(lines) + "\n") if lines else ""
    if append and Path(path).exists():
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)
    else:
        Path(path).write_text(text, encoding="utf-8")
    return n


def export_chrome_cookies_cdp(browser, cfg, domains=LOGIN_DOMAINS):
    """Export decrypted cookies from a Chromium browser via DevTools.

    Returns True, :data:`CHROME_RUNNING` if the browser must be closed first,
    or False. Headless Chrome is pointed at the *real* profile directory:
    copying the profile elsewhere breaks App-Bound Encryption key unwrapping
    on Windows and yields zero cookies.
    """
    import socket as _socket

    exe = find_chromium_binary(browser)
    if not exe:
        log(f"Could not locate {browser.title()} for cookie export.", "WARN")
        return False

    user_data = browser_profile_paths().get(browser)
    if not user_data or not Path(user_data).exists():
        log(f"{browser.title()} profile folder not found.", "WARN")
        return False

    profile = cfg.get("chrome_profile", "Default")
    proc = None
    try:
        s = _socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()

        log(f"Asking {browser.title()} to decrypt its own cookies (DevTools)...", "UPDATE")
        proc = subprocess.Popen(
            [exe, f"--user-data-dir={user_data}", f"--profile-directory={profile}",
             "--headless=new", f"--remote-debugging-port={port}",
             "--remote-allow-origins=*", "--no-first-run",
             "--no-default-browser-check", "--disable-gpu",
             "--disable-extensions", "--disable-sync", "--mute-audio",
             "--window-position=-32000,-32000", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=NO_WINDOW,
        )

        ws_url = None
        for _ in range(30):  # ~15s
            if proc.poll() is not None:
                break
            try:
                with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as r:
                    ws_url = json.loads(r.read().decode()).get("webSocketDebuggerUrl")
                if ws_url:
                    break
            except Exception:
                time.sleep(0.5)
        if not ws_url:
            log(f"{browser.title()} is still running — DevTools session couldn't start.", "WARN")
            return CHROME_RUNNING

        ws = _MiniWS(ws_url, timeout=25)
        try:
            cookies = _cdp_call(ws, 1, "Network.getAllCookies").get("cookies", [])
            if not cookies:
                cookies = _cdp_call(ws, 2, "Storage.getCookies").get("cookies", [])
        finally:
            ws.close()

        if not cookies:
            log("No cookies returned by DevTools (is this the profile you're logged in on?)", "WARN")
            return False

        wanted = [c for c in cookies
                  if any(d in (c.get("domain", "") or "") for d in domains)]
        n = cdp_cookies_to_netscape(wanted or cookies, COOKIES_FILE)
        log(f"Exported {n} cookie(s) from {browser.title()} -> {COOKIES_FILE.name}", "OK")
        return n > 0

    except Exception as e:
        log(f"Chrome cookie export failed: {e}", "ERROR")
        return False
    finally:
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


# ── CACHE REFRESH ────────────────────────────────────────────────────────────

def _export_via_ytdlp(browser, spec, interactive):
    """Let yt-dlp read the browser's cookie store directly."""
    for attempt in (1, 2):
        log(f"Exporting login cookies from {browser}...", "UPDATE")
        if IS_MAC and browser == "safari":
            log("  Safari cookies need Full Disk Access for your terminal "
                "(System Settings > Privacy & Security > Full Disk Access).", "INFO")
        elif IS_MAC and browser in CHROMIUM_BROWSERS:
            log("  macOS may ask for Keychain access — that is Chrome's cookie key.", "INFO")

        rc, out = run_quiet(
            [str(YTDLP_EXE), "--cookies-from-browser", spec,
             "--cookies", str(COOKIES_FILE),
             "--flat-playlist", "--playlist-items", "1", "--simulate",
             "--no-warnings", "https://www.instagram.com/instagram/"],
            timeout=120,
        )
        low = out.lower()
        locked = any(m in low for m in COOKIE_LOCK_MARKERS)
        if not locked and cookie_cache_valid():
            log(f"Login cookies cached: {COOKIES_FILE.name} (source: {browser})", "OK")
            return True
        if locked:
            log(f"{browser.title()} is locking its cookie database — "
                "cookies can't be read while it runs.", "WARN")
            if interactive and attempt == 1:
                try:
                    input(f"  Close {browser.title()} COMPLETELY, then press Enter to retry... ")
                    continue
                except EOFError:
                    pass
        break
    return False


def refresh_cookie_cache(cfg, interactive=True):
    """Export browser login cookies to tools/cookies.txt.

    Returns True if a usable cache exists afterwards.
    """
    b = resolve_cookie_browser(cfg)
    spec = cookie_browser_spec(b) if b else None
    if not spec:
        if b == "zen":
            log("Zen profile with cookies not found — is Zen installed and used?", "ERROR")
        return cookie_cache_valid()

    # Windows Chromium: App-Bound Encryption means DevTools is the only route.
    if IS_WIN and b in CHROMIUM_BROWSERS:
        for attempt in (1, 2):
            try:
                COOKIES_FILE.unlink(missing_ok=True)
            except Exception:
                pass
            result = export_chrome_cookies_cdp(b, cfg)
            if result is True and cookie_cache_valid():
                return True
            if result == CHROME_RUNNING and interactive and attempt == 1:
                try:
                    input(f"  Close {b.title()} COMPLETELY (window + tray icon), then press Enter... ")
                    continue
                except EOFError:
                    pass
            break
        if cookie_cache_valid():
            log("Using previously cached login cookies (may be stale).", "WARN")
            return True
        log(f"Could not export cookies from {b.title()}. Tip: log in with "
            "Firefox/Zen and select it via menu [11].", "ERROR")
        return False

    # Everywhere else yt-dlp can read the store itself. On macOS Chromium,
    # fall back to DevTools if the Keychain read fails or is denied.
    if _export_via_ytdlp(b, spec, interactive):
        return True

    if IS_MAC and b in CHROMIUM_BROWSERS:
        log("Keychain read did not produce cookies — trying the DevTools route...", "WARN")
        if export_chrome_cookies_cdp(b, cfg) is True and cookie_cache_valid():
            return True

    if cookie_cache_valid():
        log("Using previously cached login cookies (may be stale).", "WARN")
        return True
    log("Could not export login cookies.", "ERROR")
    return False


def choose_cookie_browser(cfg):
    """Menu: pick which browser to borrow login cookies from."""
    found = detect_installed_browsers()
    items = [(b, f"{b.title():<8}{' (installed)' if b in found else ''}")
             for b in browser_order()]
    items.append(("auto", "Auto-detect"))
    items.append(("none", "Disable login cookies"))
    picked = pick_from_list("Login Cookie Browser", items,
                            cfg.get("cookies_browser", "auto"))
    if not picked:
        return
    cfg["cookies_browser"] = picked
    save_config(cfg)
    log(f"Login cookies will be read from: {picked}", "OK")
    # The old cache belongs to the previous browser — rebuild it now.
    try:
        COOKIES_FILE.unlink(missing_ok=True)
    except Exception:
        pass
    if picked != "none":
        refresh_cookie_cache(cfg)
