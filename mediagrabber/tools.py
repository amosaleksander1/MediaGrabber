"""Bundled tool management: where each binary comes from per platform, and
how it gets downloaded, unpacked and kept up to date.

Adding a platform means adding a branch in the ``*_source()`` functions —
the update logic below is platform-agnostic.
"""

import os
import shutil
import sys
import tarfile
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed

from .config import (DENO_EXE, FFMPEG_EXE, FFPROBE_EXE, GALLERYDL_EXE,
                     TOOLS_DIR, UPDATE_INTERVAL_DAYS, YTDLP_EXE,
                     load_versions, update_versions)
from .net import (download_file, fetch_json, resolve_redirect,
                  set_progress)
from .platform_support import (ARCH, IS_MAC, IS_WIN, macos_version,
                               prepare_binary)
from .shell import run_quiet
from .ui import log

# ── RELEASE FEEDS ────────────────────────────────────────────────────────────

YTDLP_RELEASE_API = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
FFMPEG_RELEASE_API = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest"
DENO_RELEASE_API = "https://api.github.com/repos/denoland/deno/releases/latest"
# gallery-dl binaries live in the separate gdl-org/builds repo.
GALLERYDL_RELEASE_API = "https://api.github.com/repos/gdl-org/builds/releases/latest"

# BtbN publishes Windows and Linux only. macOS builds come from Martin Riedl's
# build server, which exposes "latest" as a redirect to a versioned path.
FFMPEG_MACOS_BASE = "https://ffmpeg.martin-riedl.de/redirect/latest/macos"


# ── SOURCE RESOLUTION ────────────────────────────────────────────────────────

def ytdlp_asset_name():
    """yt-dlp publishes a per-platform single-file build.

    ``yt-dlp_macos`` is a universal2 binary (arm64 + x86_64 in one file), so
    one asset covers both Apple Silicon and Intel. It requires macOS 12+.
    """
    if IS_WIN:
        return "yt-dlp.exe"
    if IS_MAC:
        return "yt-dlp_macos"
    return "yt-dlp_linux_aarch64" if ARCH == "arm64" else "yt-dlp"


def ytdlp_download_url():
    return ("https://github.com/yt-dlp/yt-dlp/releases/latest/download/"
            + ytdlp_asset_name())


def gallerydl_asset_name():
    """gdl-org/builds ships windows-x64, linux-x64 and macos-arm64 binaries.

    There is deliberately no Intel-macOS entry: no such binary is published,
    so :func:`update_gallerydl` falls back to a self-contained pip install.
    """
    if IS_WIN:
        return "gallery-dl_windows.exe"
    if IS_MAC:
        return "gallery-dl_macos" if ARCH == "arm64" else None
    return "gallery-dl_linux"


def deno_asset_name():
    if IS_WIN:
        return "deno-x86_64-pc-windows-msvc.zip"
    if IS_MAC:
        return ("deno-aarch64-apple-darwin.zip" if ARCH == "arm64"
                else "deno-x86_64-apple-darwin.zip")
    return ("deno-aarch64-unknown-linux-gnu.zip" if ARCH == "arm64"
            else "deno-x86_64-unknown-linux-gnu.zip")


def ffmpeg_macos_urls():
    """(ffmpeg_url, ffprobe_url) for this Mac's architecture."""
    slug = "arm64" if ARCH == "arm64" else "amd64"
    return (f"{FFMPEG_MACOS_BASE}/{slug}/release/ffmpeg.zip",
            f"{FFMPEG_MACOS_BASE}/{slug}/release/ffprobe.zip")


# ── yt-dlp ───────────────────────────────────────────────────────────────────

def update_ytdlp():
    versions = load_versions()
    local_ver = versions.get("yt-dlp", "none")
    try:
        log("Checking yt-dlp for updates...", "UPDATE")
        remote_ver = fetch_json(YTDLP_RELEASE_API)["tag_name"]

        if local_ver == remote_ver and YTDLP_EXE.exists():
            log(f"yt-dlp is up to date ({remote_ver})", "OK")
            return True

        log(f"Updating yt-dlp: {local_ver} -> {remote_ver}", "UPDATE")
        if download_file(ytdlp_download_url(), YTDLP_EXE, YTDLP_EXE.name):
            prepare_binary(YTDLP_EXE)
            update_versions(**{"yt-dlp": remote_ver})
            return True
        return False
    except Exception as e:
        if YTDLP_EXE.exists():
            log(f"Update check failed ({e}), using existing yt-dlp", "WARN")
            return True
        log(f"Cannot fetch yt-dlp and no local copy exists: {e}", "ERROR")
        return False


# ── ffmpeg / ffprobe ─────────────────────────────────────────────────────────

def _update_ffmpeg_macos(versions):
    """macOS: two single-binary zips from Martin Riedl's build server.

    'latest' is a redirect to a versioned path like
    ``/download/macos/arm64/1785863997_9.0/ffmpeg.zip`` — we use that path
    segment as the version key, since there is no version API.
    """
    ffmpeg_url, ffprobe_url = ffmpeg_macos_urls()
    local_ver = versions.get("ffmpeg", "none")

    try:
        final = resolve_redirect(ffmpeg_url)
        remote_ver = final.rstrip("/").split("/")[-2]
    except Exception:
        remote_ver = "unknown"

    if (local_ver == remote_ver and remote_ver != "unknown"
            and FFMPEG_EXE.exists() and FFPROBE_EXE.exists()):
        log(f"ffmpeg is up to date ({remote_ver})", "OK")
        return True

    log(f"Updating ffmpeg: {local_ver} -> {remote_ver} (macOS {ARCH})", "UPDATE")

    got = 0
    for url, target in ((ffmpeg_url, FFMPEG_EXE), (ffprobe_url, FFPROBE_EXE)):
        zip_path = TOOLS_DIR / f"{target.name}_temp.zip"
        if not download_file(url, zip_path, f"{target.name} (macOS {ARCH})"):
            continue
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                for name in zf.namelist():
                    if os.path.basename(name) == target.name:
                        with zf.open(name) as src, open(target, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                        prepare_binary(target)
                        log(f"  Extracted {target.name}", "OK")
                        got += 1
                        break
        except Exception as e:
            log(f"  Could not unpack {target.name}: {e}", "ERROR")
        finally:
            try:
                zip_path.unlink(missing_ok=True)
            except Exception:
                pass

    if got == 2:
        update_versions(ffmpeg=remote_ver)
        log(f"ffmpeg updated to {remote_ver}", "OK")
        return True
    return FFMPEG_EXE.exists() and FFPROBE_EXE.exists()


def _update_ffmpeg_btbn(versions):
    """Windows/Linux: BtbN's combined archive."""
    local_ver = versions.get("ffmpeg", "none")
    data = fetch_json(FFMPEG_RELEASE_API)
    # BtbN uses a rolling "latest" tag, so tag_name is useless as a version.
    remote_ver = data.get("published_at", data["tag_name"])

    if local_ver == remote_ver and FFMPEG_EXE.exists() and FFPROBE_EXE.exists():
        log(f"ffmpeg is up to date ({remote_ver})", "OK")
        return True

    log(f"Updating ffmpeg: {local_ver} -> {remote_ver}", "UPDATE")

    if IS_WIN:
        want_os, want_ext = "win64", ".zip"
        wanted = ("ffmpeg.exe", "ffprobe.exe")
    else:
        want_os = "linuxarm64" if ARCH == "arm64" else "linux64"
        want_ext = ".tar.xz"
        wanted = ("ffmpeg", "ffprobe")

    asset_url = None
    for asset in data.get("assets", []):
        name = asset["name"]
        if (want_os in name and "gpl" in name and name.endswith(want_ext)
                and "shared" not in name):
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
    try:
        if IS_WIN:
            with zipfile.ZipFile(pkg_path, "r") as zf:
                for name in zf.namelist():
                    base = os.path.basename(name)
                    if base in wanted:
                        with zf.open(name) as src, open(TOOLS_DIR / base, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                        log(f"  Extracted {base}", "OK")
                        extracted = True
        else:
            with tarfile.open(pkg_path, "r:xz") as tf:
                for member in tf.getmembers():
                    base = os.path.basename(member.name)
                    if base in wanted and member.isfile():
                        target = TOOLS_DIR / base
                        with tf.extractfile(member) as src, open(target, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                        prepare_binary(target)
                        log(f"  Extracted {base}", "OK")
                        extracted = True
    finally:
        try:
            pkg_path.unlink(missing_ok=True)
        except Exception:
            pass

    if extracted:
        update_versions(ffmpeg=remote_ver)
        log(f"ffmpeg updated to {remote_ver}", "OK")
        return True
    log("Could not find ffmpeg in the archive", "ERROR")
    return FFMPEG_EXE.exists()


def update_ffmpeg():
    versions = load_versions()
    try:
        log("Checking ffmpeg for updates...", "UPDATE")
        if IS_MAC:
            return _update_ffmpeg_macos(versions)
        return _update_ffmpeg_btbn(versions)
    except Exception as e:
        if FFMPEG_EXE.exists():
            log(f"Update check failed ({e}), using existing ffmpeg", "WARN")
            return True
        log(f"Cannot fetch ffmpeg and no local copy exists: {e}", "ERROR")
        return False


# ── Deno (yt-dlp's JS runtime for YouTube extraction) ────────────────────────

def update_deno():
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
        want = deno_asset_name()
        asset_url = next((a["browser_download_url"] for a in data.get("assets", [])
                          if a["name"] == want), None)
        if not asset_url:
            log(f"Could not find {want} in the Deno release", "ERROR")
            return DENO_EXE.exists()

        zip_path = TOOLS_DIR / "deno_temp.zip"
        if not download_file(asset_url, zip_path, "Deno"):
            return DENO_EXE.exists()

        log("Extracting Deno...", "UPDATE")
        extracted = False
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                for name in zf.namelist():
                    if os.path.basename(name).lower() == DENO_EXE.name.lower():
                        with zf.open(name) as src, open(DENO_EXE, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                        prepare_binary(DENO_EXE)
                        log(f"  Extracted {DENO_EXE.name}", "OK")
                        extracted = True
                        break
        finally:
            try:
                zip_path.unlink(missing_ok=True)
            except Exception:
                pass

        if extracted:
            update_versions(deno=remote_ver)
            log(f"Deno updated to {remote_ver}", "OK")
            return True
        log("Could not find the deno binary in the archive", "ERROR")
        return DENO_EXE.exists()
    except Exception as e:
        if DENO_EXE.exists():
            log(f"Update check failed ({e}), using existing Deno", "WARN")
            return True
        log(f"Cannot fetch Deno and no local copy exists: {e}", "ERROR")
        return False


# ── gallery-dl (+ pip fallback where no binary is published) ─────────────────

GALLERYDL_PKG_DIR = TOOLS_DIR / "gallery-dl-pkg"


def _system_python():
    """A Python interpreter usable for the pip fallback.

    When frozen by PyInstaller ``sys.executable`` is our own binary, so look
    for a real interpreter on PATH instead.
    """
    if not getattr(sys, "frozen", False):
        return sys.executable
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _write_gallerydl_shim():
    """Write a launcher that runs the pip-installed gallery-dl package."""
    py = _system_python()
    if not py:
        return False
    if IS_WIN:
        shim = TOOLS_DIR / "gallery-dl.cmd"
        shim.write_text(
            "@echo off\r\n"
            f'set "PYTHONPATH={GALLERYDL_PKG_DIR}"\r\n'
            f'"{py}" -m gallery_dl %*\r\n',
            encoding="utf-8")
    else:
        shim = GALLERYDL_EXE
        shim.write_text(
            "#!/bin/sh\n"
            f'PYTHONPATH="{GALLERYDL_PKG_DIR}" exec "{py}" -m gallery_dl "$@"\n',
            encoding="utf-8")
        prepare_binary(shim)
    return True


def _install_gallerydl_via_pip():
    """Install gallery-dl into tools/gallery-dl-pkg and write a shim.

    Used on Intel Macs (no published binary) and on Linux distros whose glibc
    is older than the prebuilt binary requires. Nothing is installed
    system-wide — the package tree lives inside tools/.
    """
    py = _system_python()
    if not py:
        log("No system Python found for the gallery-dl fallback install.", "ERROR")
        return False

    log("No prebuilt gallery-dl for this platform — installing via pip "
        "into tools/ (nothing is installed system-wide)...", "UPDATE")
    rc, out = run_quiet(
        [py, "-m", "pip", "install", "--upgrade", "--no-input",
         "--target", str(GALLERYDL_PKG_DIR), "gallery-dl"],
        timeout=600,
    )
    if rc != 0:
        log(f"pip install gallery-dl failed: {out.strip()[-300:]}", "ERROR")
        return False
    if not _write_gallerydl_shim():
        return False
    log("gallery-dl installed via pip fallback.", "OK")
    return True


def gallerydl_command():
    """The command prefix used to invoke gallery-dl on this platform."""
    if IS_WIN and not GALLERYDL_EXE.exists():
        shim = TOOLS_DIR / "gallery-dl.cmd"
        if shim.exists():
            return [str(shim)]
    return [str(GALLERYDL_EXE)]


def gallerydl_available():
    return GALLERYDL_EXE.exists() or (TOOLS_DIR / "gallery-dl.cmd").exists()


def update_gallerydl():
    versions = load_versions()
    local_ver = versions.get("gallery-dl", "none")
    want = gallerydl_asset_name()

    # No published binary for this platform (Intel macOS) — use pip.
    if want is None:
        if gallerydl_available():
            log("gallery-dl (pip build) present.", "OK")
            return True
        return _install_gallerydl_via_pip()

    try:
        log("Checking gallery-dl for updates...", "UPDATE")
        data = fetch_json(GALLERYDL_RELEASE_API)
        remote_ver = data["tag_name"]

        if local_ver == remote_ver and GALLERYDL_EXE.exists():
            log(f"gallery-dl is up to date ({remote_ver})", "OK")
            return True

        log(f"Updating gallery-dl: {local_ver} -> {remote_ver}", "UPDATE")
        asset_url = next((a["browser_download_url"] for a in data.get("assets", [])
                          if a["name"].lower() == want.lower()), None)
        if not asset_url:
            log(f"Could not find {want} in the release", "ERROR")
            return GALLERYDL_EXE.exists()

        if download_file(asset_url, GALLERYDL_EXE, GALLERYDL_EXE.name):
            prepare_binary(GALLERYDL_EXE)
            update_versions(**{"gallery-dl": remote_ver})
            return True
        return GALLERYDL_EXE.exists()
    except Exception as e:
        if gallerydl_available():
            log(f"Update check failed ({e}), using existing gallery-dl", "WARN")
            return True
        log(f"Cannot fetch gallery-dl and no local copy exists: {e}", "ERROR")
        return False


def repair_gallerydl():
    """Recover from a gallery-dl binary that downloads but cannot execute.

    The realistic cause is an ABI mismatch: the Linux binary needs glibc 2.38+,
    and the macOS binary is arm64-only. Both are fixed by the pip fallback.
    """
    log("gallery-dl binary will not run here — switching to the pip fallback.", "WARN")
    try:
        if GALLERYDL_EXE.exists():
            GALLERYDL_EXE.unlink()
    except Exception:
        pass
    return _install_gallerydl_via_pip()


# ── MISSING SYSTEM PYTHON (Intel Macs) ───────────────────────────────────────

def needs_system_python():
    """True when this machine can only get gallery-dl through pip.

    Upstream publishes no gallery-dl binary for Intel Macs, so those fall back
    to a pip install into tools/ — and that is the one and only place where a
    MediaGrabber user needs anything installed on their system. The app itself
    carries its own Python inside the binary.
    """
    return gallerydl_asset_name() is None and not _system_python()


def python_install_advice():
    """(what is missing, the command that fixes it) for this platform."""
    if IS_MAC:
        return ("Apple's command line tools, which include Python 3",
                ["xcode-select", "--install"])
    if IS_WIN:                      # a binary is published, so this is unlikely
        return ("Python 3 from python.org", None)
    return ("Python 3 from your distribution, e.g. 'sudo apt install python3'",
            None)


def offer_python_install(ask=True):
    """Explain the missing dependency and, with consent, start the installer.

    Deliberately never runs without an explicit yes, and never uses sudo: on
    macOS this hands off to Apple's own installer dialog, which asks for
    whatever it needs itself. Returns True only if Python is available now.
    """
    if not needs_system_python():
        return bool(_system_python())

    what, command = python_install_advice()
    # WARN so these survive the quiet startup - this one genuinely matters.
    log("Image posts need gallery-dl, and this Mac has no prebuilt copy of "
        "it, so it has to be installed with Python.", "WARN")
    log(f"Python 3 was not found. MediaGrabber needs {what}.", "WARN")
    log("Nothing is installed system-wide by MediaGrabber itself - only "
        "gallery-dl, into its own tools/ folder.", "INFO")

    if command is None:
        log(f"Please install {what}, then run menu [5] Tools Update.", "INFO")
        return False

    if ask:
        prompt = (f"  Run '{' '.join(command)}' now? macOS will open its own "
                  f"installer window. [y/N] ")
        try:
            answer = input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            log("No answer - skipping. Run menu [5] when Python is installed.",
                "WARN")
            return False
        if answer not in ("y", "yes"):
            log("Skipped. Image-only posts may fail until Python is "
                "installed; run menu [5] afterwards.", "WARN")
            return False

    log(f"Starting {' '.join(command)}...", "UPDATE")
    try:
        run_quiet(command, timeout=60)
    except Exception as e:
        log(f"Could not start the installer: {e}", "ERROR")
        return False

    if _system_python():
        log("Python 3 is available now.", "OK")
        return True

    # The macOS installer runs in its own window and takes minutes; the tool
    # will not appear on PATH until the user finishes it there.
    log("Finish the macOS installer window that just opened, then run "
        "menu [5] Tools Update to install gallery-dl.", "INFO")
    return False


# ── ORCHESTRATION ────────────────────────────────────────────────────────────

def tools_present():
    return (all(t.exists() for t in (YTDLP_EXE, FFMPEG_EXE, FFPROBE_EXE, DENO_EXE))
            and gallerydl_available())


def run_updates(cfg, force=False, interactive=True):
    """Check/download every bundled tool.

    Throttled: if all tools exist and the last check was under
    ``UPDATE_INTERVAL_DAYS`` ago this returns immediately, so startup is
    instant. ``force=True`` (menu [5] Tools Update, or after a tool failure)
    always checks. ``interactive=False`` suppresses the one question this can
    ask, for piped runs and CI where there is nobody to answer it.
    """
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
                log(f"Tools ready — next update check in {max(days_left, 0):.0f} "
                    "day(s) (menu [5] to force)", "OK")
                return True

    if IS_MAC:
        mv = macos_version()
        if mv and mv[0] < 12:
            log(f"macOS {mv[0]}.{mv[1]} detected — the official yt-dlp macOS "
                "build requires macOS 12+. Downloads may fail.", "WARN")

    results = update_all()

    # Asked here rather than inside the updater: that one runs on a worker
    # thread alongside three others, and a yes/no prompt from a thread would
    # be buried under their output and read from the same stdin they do.
    if not results.get("gallery-dl") and needs_system_python():
        if offer_python_install(ask=interactive):
            results["gallery-dl"] = update_gallerydl()

    update_versions(_last_check=time.time())
    return all(results.values())


#: Every bundled tool and the function that fetches it. Order is only used for
#: the closing summary; they all start at once.
def _updaters():
    return (("yt-dlp", update_ytdlp),
            ("ffmpeg", update_ffmpeg),
            ("deno", update_deno),
            ("gallery-dl", update_gallerydl))


def _safely(name, fn):
    """One tool failing must not take the other three down with it."""
    try:
        return bool(fn())
    except Exception as e:
        log(f"{name} update raised: {e}", "ERROR")
        return False


def update_all():
    """Fetch every tool at once rather than one after another.

    The wait here is network, not CPU — four independent HTTP downloads that
    each spend their time blocked on a socket. Threads turn four waits into
    one, which on a slow connection is the difference between a minute of
    staring at a progress bar and a few seconds.

    Three things make it safe, and each is arranged elsewhere rather than here:
    the version file is written through ``update_versions()``, which re-reads
    and merges under a lock (four threads doing load-modify-write on one file
    would silently drop entries); ``log()`` holds a lock across its print, so
    messages cannot interleave mid-line; and the live byte counter is switched
    off for the duration, because a line redrawn with a carriage return assumes
    it owns the bottom of the terminal and four of them do not.

    Returns {tool name: succeeded}.
    """
    results = {}
    had_progress = set_progress(False)
    try:
        updaters = _updaters()
        with ThreadPoolExecutor(max_workers=len(updaters)) as pool:
            pending = {pool.submit(_safely, name, fn): name
                       for name, fn in updaters}
            for future in as_completed(pending):
                name = pending[future]
                results[name] = future.result()
                log(f"  {name}: {'ready' if results[name] else 'failed'}",
                    "OK" if results[name] else "ERROR")
    finally:
        set_progress(had_progress)
    return results
