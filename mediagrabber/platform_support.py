"""Everything that differs between Windows, macOS and Linux lives here.

Nothing else in the codebase should test ``sys.platform`` directly — import
the flags and helpers from this module instead. That is what makes adding a
fourth platform a one-file change.
"""

import os
import platform as _platform
import subprocess
import sys
from pathlib import Path

# ── PLATFORM FLAGS ───────────────────────────────────────────────────────────

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
IS_LINUX = not IS_WIN and not IS_MAC

#: "arm64" on Apple Silicon, "x86_64" on Intel/AMD. Normalised across the
#: several spellings the stdlib returns (aarch64/arm64, x86_64/amd64/AMD64).
_RAW_ARCH = (_platform.machine() or "").lower()
if _RAW_ARCH in ("arm64", "aarch64"):
    ARCH = "arm64"
elif _RAW_ARCH in ("x86_64", "amd64", "x64"):
    ARCH = "x86_64"
else:
    ARCH = _RAW_ARCH or "unknown"

#: Short platform tag used for release artifact names and log lines.
if IS_WIN:
    PLATFORM_TAG = f"windows-{'arm64' if ARCH == 'arm64' else 'x64'}"
elif IS_MAC:
    PLATFORM_TAG = f"macos-{'arm64' if ARCH == 'arm64' else 'x64'}"
else:
    PLATFORM_TAG = f"linux-{'arm64' if ARCH == 'arm64' else 'x64'}"

OS_LABEL = "Windows" if IS_WIN else ("macOS" if IS_MAC else "Linux")

#: Executable suffix for bundled tools.
EXE = ".exe" if IS_WIN else ""

#: Passed as ``creationflags`` so helper processes never flash a console
#: window on Windows. Zero (ignored) everywhere else.
NO_WINDOW = subprocess.CREATE_NO_WINDOW if IS_WIN else 0


def macos_version():
    """(major, minor) of the running macOS, or None when not on macOS."""
    if not IS_MAC:
        return None
    try:
        parts = _platform.mac_ver()[0].split(".")
        return (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
    except Exception:
        return None


# ── FILE PERMISSIONS / GATEKEEPER ────────────────────────────────────────────

def make_executable(path):
    """chmod +x on POSIX (no-op on Windows)."""
    if IS_WIN:
        return
    try:
        Path(path).chmod(0o755)
    except Exception:
        pass


def clear_quarantine(path):
    """Strip macOS's ``com.apple.quarantine`` extended attribute.

    Anything downloaded over HTTP gets quarantined by macOS; launching it then
    raises the "cannot be opened because the developer cannot be verified"
    Gatekeeper dialog — fatal for a tool we spawn non-interactively. Removing
    the attribute on binaries *we* just downloaded is the same trust decision
    the user already made by running MediaGrabber.
    """
    if not IS_MAC:
        return
    try:
        subprocess.run(["xattr", "-dr", "com.apple.quarantine", str(path)],
                       capture_output=True, timeout=30)
    except Exception:
        pass


def prepare_binary(path):
    """Make a freshly downloaded tool runnable on this platform."""
    make_executable(path)
    clear_quarantine(path)


# ── OS INTEGRATION ───────────────────────────────────────────────────────────

def open_path(path):
    """Open a file or folder with the OS default handler."""
    target = str(path)
    if IS_WIN:
        os.startfile(target)  # noqa: S606 — Windows-only API
    elif IS_MAC:
        subprocess.Popen(["open", target],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.Popen(["xdg-open", target],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def pick_folder_dialog(initial=None):
    """Show a native folder picker. Returns a path string, or None.

    Windows uses a PowerShell WinForms dialog, macOS uses AppleScript's
    ``choose folder``. Linux has no dependency-free equivalent, so it returns
    None and the caller falls back to typing a path.
    """
    if IS_WIN:
        ps_cmd = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
            f'$f.SelectedPath = "{initial or ""}"; '
            '$f.Description = "Select download output folder"; '
            'if ($f.ShowDialog() -eq "OK") { $f.SelectedPath } else { "" }'
        )
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd],
                               capture_output=True, text=True, timeout=120,
                               creationflags=NO_WINDOW)
            return r.stdout.strip() or None
        except Exception:
            return None

    if IS_MAC:
        default = f' default location POSIX file "{initial}"' if initial else ""
        script = (f'POSIX path of (choose folder with prompt '
                  f'"Select download output folder"{default})')
        try:
            r = subprocess.run(["osascript", "-e", script],
                               capture_output=True, text=True, timeout=120)
            picked = r.stdout.strip()
            # AppleScript returns a trailing slash; Path() copes, but strip it
            # so the value we persist matches what the user would type.
            return picked.rstrip("/") or None
        except Exception:
            return None

    return None


# ── NON-BLOCKING KEYPRESS (graceful stop) ────────────────────────────────────

if IS_WIN:
    import msvcrt  # noqa: E402  — Windows-only stdlib module


def stop_requested():
    """Non-blocking check for the user pressing Q.

    Windows reads the raw keyboard buffer, so a bare Q works. POSIX (macOS and
    Linux) reads a line from stdin, so it is Q-then-Enter.
    """
    try:
        if IS_WIN:
            while msvcrt.kbhit():
                if msvcrt.getwch().lower() == "q":
                    return True
        else:
            import select
            r, _, _ = select.select([sys.stdin], [], [], 0)
            if r and sys.stdin.readline().strip().lower() == "q":
                return True
    except Exception:
        pass
    return False


def stop_hint_text():
    return "Press Q to stop downloads" + ("" if IS_WIN else " (Q then Enter)")


def enable_ansi():
    """Turn on ANSI escape processing in legacy Windows consoles."""
    if IS_WIN:
        os.system("")  # noqa: S605 — documented cmd.exe VT-enable trick


# ── APP LOCATION ─────────────────────────────────────────────────────────────

def app_dir():
    """Folder the app lives in — next to the frozen binary, or the repo root."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    # .../MediaGrabber/mediagrabber/platform_support.py -> .../MediaGrabber
    return Path(__file__).resolve().parent.parent
