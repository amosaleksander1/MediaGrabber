"""Startup and on-demand health check: do the tools run, is login alive."""

from .config import DENO_EXE, FFMPEG_EXE, FFPROBE_EXE, YTDLP_EXE
from .cookies import (COOKIE_LOCK_MARKERS, cookie_args, cookie_cache_valid,
                      refresh_cookie_cache, resolve_cookie_browser)
from .config import COOKIES_FILE
from .platform_support import IS_MAC, OS_LABEL, PLATFORM_TAG, macos_version
from .shell import run_quiet
from .tools import gallerydl_available, gallerydl_command, repair_gallerydl
from .ui import log


def _check_tool(name, exe, vargs, quick, runner=None):
    """Returns True if the tool is usable (or assumed so in quick mode)."""
    if runner is None and not exe.exists():
        log(f"  [MISSING] {name} — run menu [7] to install", "ERROR")
        return False
    if quick:
        log(f"  [OK] {name}: installed", "OK")
        return True
    rc, out = run_quiet((runner or [str(exe)]) + vargs, timeout=30)
    if rc == 0:
        ver = out.strip().splitlines()[0][:60] if out.strip() else "ok"
        log(f"  [OK] {name}: {ver}", "OK")
        return True
    log(f"  [FAIL] {name} did not run: {out.strip()[:120]}", "ERROR")
    return (rc, out)


def run_checkup(cfg, probe_auth=True, quick=False):
    """Verify every tool runs and the Instagram login session works.

    ``quick=True`` (cold start) does existence checks only, and skips the slow
    live Instagram probe unless no cookie cache exists yet.
    """
    log("Running system checkup..." + (" (quick)" if quick else ""), "HEADER")
    log(f"  Platform: {OS_LABEL} ({PLATFORM_TAG})", "INFO")
    if IS_MAC:
        mv = macos_version()
        if mv:
            log(f"  macOS {mv[0]}.{mv[1]}"
                + ("" if mv[0] >= 12 else "  [!] yt-dlp's macOS build needs 12+"),
                "INFO" if not mv or mv[0] >= 12 else "WARN")

    ok = True

    for name, exe, vargs in (("yt-dlp", YTDLP_EXE, ["--version"]),
                             ("ffmpeg", FFMPEG_EXE, ["-version"]),
                             ("ffprobe", FFPROBE_EXE, ["-version"]),
                             ("deno", DENO_EXE, ["--version"])):
        if _check_tool(name, exe, vargs, quick) is not True:
            ok = False

    # gallery-dl may be a binary or a pip shim, so it is checked separately.
    if not gallerydl_available():
        log("  [MISSING] gallery-dl — run menu [7] to install", "ERROR")
        ok = False
    elif quick:
        log("  [OK] gallery-dl: installed", "OK")
    else:
        rc, out = run_quiet(gallerydl_command() + ["--version"], timeout=60)
        if rc == 0:
            log(f"  [OK] gallery-dl: {out.strip().splitlines()[0][:60]}", "OK")
        else:
            log(f"  [FAIL] gallery-dl did not run: {out.strip()[:120]}", "ERROR")
            # Wrong architecture / too-old glibc is recoverable via pip.
            if repair_gallerydl():
                rc2, out2 = run_quiet(gallerydl_command() + ["--version"], timeout=60)
                if rc2 == 0:
                    log(f"  [OK] gallery-dl (pip): {out2.strip().splitlines()[0][:60]}", "OK")
                else:
                    ok = False
            else:
                ok = False

    if quick:
        probe_auth = probe_auth and not cookie_cache_valid()

    # ── Login / auth ──
    browser = resolve_cookie_browser(cfg)
    if browser is None and not cookie_cache_valid():
        log("  [WARN] No login cookie source — Instagram carousels will fail.", "WARN")
        log("         Set one via menu [11].", "INFO")
        ok = False
        return _finish(ok)

    if not cookie_cache_valid():
        refresh_cookie_cache(cfg)
    if cookie_cache_valid():
        log(f"  Login cookies: cached file ({COOKIES_FILE.name}, source: {browser})", "INFO")
    else:
        log(f"  Login cookies: live from {browser} (no cache — may fail while browser is open)", "WARN")

    if probe_auth and gallerydl_available():
        for probe_round in (1, 2):
            log("  Probing Instagram login session...", "INFO")
            rc, out = run_quiet(
                gallerydl_command() + cookie_args(cfg) +
                ["--simulate", "--range", "1", "https://www.instagram.com/instagram/"],
                timeout=90,
            )
            low = out.lower()
            if "login" in low or "unauthorized" in low or "401" in low:
                if probe_round == 1 and cookie_cache_valid():
                    log("  Cached cookies look stale — refreshing from browser...", "WARN")
                    try:
                        COOKIES_FILE.unlink(missing_ok=True)
                    except Exception:
                        pass
                    if refresh_cookie_cache(cfg):
                        continue
                log(f"  [FAIL] Instagram session not logged in (source: {browser}).", "ERROR")
                log(f"         Log in at instagram.com in {browser}, then re-run menu [10].", "INFO")
                ok = False
            elif rc == 0:
                log("  [OK] Instagram login session works.", "OK")
            else:
                tail = out.strip().splitlines()[-1][:100] if out.strip() else "no output"
                log(f"  [WARN] Auth probe inconclusive: {tail}", "WARN")
                if any(m in low for m in COOKIE_LOCK_MARKERS):
                    log(f"         {browser.title()} is locking its cookie DB — "
                        "close it fully and re-run menu [10].", "INFO")
            break

    return _finish(ok)


def _finish(ok):
    if ok:
        log("Checkup passed — everything is ready.", "OK")
    else:
        log("Checkup found issues — see above.", "WARN")
    return ok
