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

from .config import (DENO_EXE, FFMPEG_EXE, FFPROBE_EXE, GALLERYDL_EXE,
                     TOOLS_DIR, UPDATE_INTERVAL_DAYS, YTDLP_EXE,
                     load_versions, save_versions)
from .net import download_file, fetch_json, resolve_redirect
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
            versions["yt-dlp"] = remote_ver
            save_versions(versions)
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
        versions["ffmpeg"] = remote_ver
        save_versions(versions)
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
        versions["ffmpeg"] = remote_ver
        save_versions(versions)
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
            versions["deno"] = remote_ver
            save_versions(versions)
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
            versions["gallery-dl"] = remote_ver
            save_versions(versions)
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


# ── ORCHESTRATION ────────────────────────────────────────────────────────────

def tools_present():
    return (all(t.exists() for t in (YTDLP_EXE, FFMPEG_EXE, FFPROBE_EXE, DENO_EXE))
            and gallerydl_available())


def run_updates(cfg, force=False):
    """Check/download every bundled tool.

    Throttled: if all tools exist and the last check was under
    ``UPDATE_INTERVAL_DAYS`` ago this returns immediately, so startup is
    instant. ``force=True`` (menu [7], or after a tool failure) always checks.
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
                    "day(s) (menu [7] to force)", "OK")
                return True

    if IS_MAC:
        mv = macos_version()
        if mv and mv[0] < 12:
            log(f"macOS {mv[0]}.{mv[1]} detected — the official yt-dlp macOS "
                "build requires macOS 12+. Downloads may fail.", "WARN")

    ok = True
    for fn in (update_ytdlp, update_ffmpeg, update_deno, update_gallerydl):
        if not fn():
            ok = False

    versions = load_versions()
    versions["_last_check"] = time.time()
    save_versions(versions)
    return ok
