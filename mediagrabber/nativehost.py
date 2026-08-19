"""Registering the bridge so a browser is willing to launch it.

A browser only starts a native-messaging host it has been told about: a small
JSON manifest naming the executable and — the part that matters — the exact
extension IDs allowed to connect. That allow-list is the security boundary, and
it is why this is a better channel than a local port: no other process, page or
extension can reach the bridge.

Where the manifest goes differs per browser and per OS, and the two engines
disagree on the field name (``allowed_origins`` with a chrome-extension:// URL
for Chromium, ``allowed_extensions`` with an add-on ID for Firefox).

Everything here is written **user-level**: per-user registry keys on Windows and
paths under the user's home elsewhere. Nothing needs admin rights and nothing
touches the system.
"""

import json
import os
from pathlib import Path

from .config import APP_DIR
from .platform_support import EXE, IS_MAC, IS_WIN

#: Reverse-DNS host name, shared by the manifest, the registry key and the
#: extension's connectNative() call. Changing it breaks every registration.
HOST_NAME = "com.mediagrabber.bridge"

#: Chrome derives an unpacked extension's ID from its folder path unless the
#: manifest carries a "key". extension/manifest.json ships that key so the ID is
#: stable across machines and installs — otherwise this allow-list could not be
#: written ahead of time.
CHROME_EXTENSION_ID = "aoeenjihnmilloleedphnegngpadbjef"
FIREFOX_EXTENSION_ID = "mediagrabber@amosaleksander"

#: Windows: HKCU subkey per browser family. macOS/Linux: directory per browser,
#: relative to the user's home.
_WIN_KEYS = {
    "chrome": r"Software\Google\Chrome\NativeMessagingHosts",
    "chromium": r"Software\Chromium\NativeMessagingHosts",
    "edge": r"Software\Microsoft\Edge\NativeMessagingHosts",
    "brave": r"Software\BraveSoftware\Brave-Browser\NativeMessagingHosts",
    "vivaldi": r"Software\Vivaldi\NativeMessagingHosts",
    "opera": r"Software\Opera Software\NativeMessagingHosts",
    "firefox": r"Software\Mozilla\NativeMessagingHosts",
    "zen": r"Software\Mozilla\NativeMessagingHosts",
}

_MAC_DIRS = {
    "chrome": "Library/Application Support/Google/Chrome/NativeMessagingHosts",
    "chromium": "Library/Application Support/Chromium/NativeMessagingHosts",
    "edge": "Library/Application Support/Microsoft Edge/NativeMessagingHosts",
    "brave": "Library/Application Support/BraveSoftware/Brave-Browser/NativeMessagingHosts",
    "vivaldi": "Library/Application Support/Vivaldi/NativeMessagingHosts",
    "opera": "Library/Application Support/com.operasoftware.Opera/NativeMessagingHosts",
    "firefox": "Library/Application Support/Mozilla/NativeMessagingHosts",
    "zen": "Library/Application Support/Mozilla/NativeMessagingHosts",
}

_LINUX_DIRS = {
    "chrome": ".config/google-chrome/NativeMessagingHosts",
    "chromium": ".config/chromium/NativeMessagingHosts",
    "edge": ".config/microsoft-edge/NativeMessagingHosts",
    "brave": ".config/BraveSoftware/Brave-Browser/NativeMessagingHosts",
    "vivaldi": ".config/vivaldi/NativeMessagingHosts",
    "opera": ".config/opera/NativeMessagingHosts",
    "firefox": ".mozilla/native-messaging-hosts",
    "zen": ".mozilla/native-messaging-hosts",
}

#: Which engine a browser belongs to — it decides the manifest's allow-list field.
_GECKO = ("firefox", "zen")

#: Safari extensions cannot use native messaging this way; they need a signed
#: app extension bundle, which is out of scope.
UNSUPPORTED = ("safari",)


def bridge_binary():
    """Absolute path to the program a browser should launch.

    Frozen builds ship ``mediagrabber-bridge`` next to ``MediaGrabber`` and that
    is the end of it. Running from source is the awkward case: Windows browsers
    will not execute a ``.py`` file as a native host — it has to be an ``.exe``
    or a ``.bat`` — so a small batch wrapper is generated and pointed at
    instead. Without this the browser silently never starts the bridge, which
    looks exactly like "the extension does nothing".
    """
    frozen = APP_DIR / f"mediagrabber-bridge{EXE}"
    if frozen.exists():
        return frozen

    script = APP_DIR / "bridge_main.py"
    if not IS_WIN:
        # A shebang plus the executable bit is enough on macOS and Linux.
        try:
            script.chmod(script.stat().st_mode | 0o755)
        except OSError:
            pass
        return script

    return _write_bat_wrapper(script)


def _write_bat_wrapper(script):
    """Generate the .bat a Windows browser can actually launch (source runs)."""
    import sys
    wrapper = APP_DIR / "tools" / "native-hosts" / "mediagrabber-bridge.bat"
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    # %* forwards the origin/manifest arguments the browser appends.
    wrapper.write_text(
        "@echo off\r\n"
        f'"{sys.executable}" "{script}" %*\r\n',
        encoding="utf-8")
    return wrapper


def manifest_dir(browser):
    """Where this browser looks for host manifests, or None if unsupported."""
    if browser in UNSUPPORTED:
        return None
    if IS_WIN:
        # The manifest may live anywhere on Windows; a registry value points at
        # it. Keep our copies together inside the app folder.
        return APP_DIR / "tools" / "native-hosts"
    table = _MAC_DIRS if IS_MAC else _LINUX_DIRS
    rel = table.get(browser)
    return (Path.home() / rel) if rel else None


def manifest_path(browser):
    d = manifest_dir(browser)
    if d is None:
        return None
    # On Windows every browser reads the same file, pointed at by its own key.
    name = f"{HOST_NAME}.json" if not IS_WIN else f"{HOST_NAME}.{browser}.json"
    return d / name


def manifest_content(browser, binary=None):
    """The manifest a given browser expects, as a dict."""
    binary = Path(binary) if binary else bridge_binary()
    doc = {
        "name": HOST_NAME,
        "description": "MediaGrabber browser bridge",
        "path": str(binary),
        "type": "stdio",
    }
    if browser in _GECKO:
        doc["allowed_extensions"] = [FIREFOX_EXTENSION_ID]
    else:
        doc["allowed_origins"] = [f"chrome-extension://{CHROME_EXTENSION_ID}/"]
    return doc


# ── WINDOWS REGISTRY ─────────────────────────────────────────────────────────

def _win_registry_key(browser):
    sub = _WIN_KEYS.get(browser)
    return f"{sub}\\{HOST_NAME}" if sub else None


def _win_write_key(browser, path):
    import winreg
    key_path = _win_registry_key(browser)
    if not key_path:
        return False
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(path))
    return True


def _win_delete_key(browser):
    import winreg
    key_path = _win_registry_key(browser)
    if not key_path:
        return False
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
        return True
    except FileNotFoundError:
        return False


# ── REGISTER / UNREGISTER ────────────────────────────────────────────────────

def register(browsers, binary=None):
    """Install the manifest for each browser. Returns [(browser, ok, detail)]."""
    results = []
    for browser in browsers:
        path = manifest_path(browser)
        if path is None:
            results.append((browser, False, "native messaging not supported"))
            continue
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(manifest_content(browser, binary), indent=2) + "\n",
                encoding="utf-8")
            if IS_WIN:
                _win_write_key(browser, path)
            results.append((browser, True, str(path)))
        except Exception as e:
            results.append((browser, False, f"{type(e).__name__}: {e}"))
    return results


def unregister(browsers):
    """Remove the manifest (and registry key) for each browser."""
    results = []
    for browser in browsers:
        path = manifest_path(browser)
        removed = False
        try:
            if path is not None and path.exists():
                os.remove(path)
                removed = True
            if IS_WIN and _win_delete_key(browser):
                removed = True
            results.append((browser, removed, "removed" if removed
                            else "nothing registered"))
        except Exception as e:
            results.append((browser, False, f"{type(e).__name__}: {e}"))
    return results


def status(browsers):
    """Report registration state, and whether the recorded path still exists.

    The manifest stores an absolute path, so moving or renaming the app folder
    breaks the connection silently — this is what catches that.
    """
    out = []
    for browser in browsers:
        path = manifest_path(browser)
        if path is None or not path.exists():
            out.append((browser, False, "not registered"))
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            recorded = Path(doc.get("path", ""))
            if not recorded.exists():
                out.append((browser, False, f"stale — {recorded} is gone; re-register"))
            else:
                out.append((browser, True, str(recorded)))
        except Exception as e:
            out.append((browser, False, f"unreadable manifest: {e}"))
    return out
