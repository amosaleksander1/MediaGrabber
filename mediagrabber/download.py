"""The download engine: yt-dlp first, gallery-dl for carousels, images and
media mode, TikTok's embed page as the last resort."""

import html as _html
import re
import time
from pathlib import Path
from urllib.request import Request, urlopen

from .config import (BROWSER_UA, DENO_EXE, NO_VIDEO_MARKERS, OUTPUT_DIR,
                     PERMANENT_ERRORS, TOOLS_DIR, TOOL_FAILURE_MARKERS,
                     YTDLP_EXE)
from .cookies import cookie_args
from .platform_support import stop_requested
from .probe import (is_carousel_candidate, is_post_url, needs_login,
                    post_base_name, probe_post, tiktok_video_id)
from .shell import popen_stream, stop_process
from .tools import (gallerydl_available, gallerydl_command, repair_gallerydl,
                    run_updates)
from .ui import C, log


class DownloadStopped(Exception):
    """Raised when the user presses Q to stop the current download."""


# ── FILE NAMING ──────────────────────────────────────────────────────────────
# yt-dlp writes to a temp name carrying the playlist index, so carousel items
# with identical titles never collide and can be renamed deterministically.

_TMP_RE = re.compile(r"^(?P<title>.+)\.__MGIDX_(?P<idx>\d*)__\._MGTMP_\.(?P<ext>.+)$")


def unique_path(parent, name):
    candidate = Path(parent) / name
    if not candidate.exists():
        return candidate
    p = Path(name)
    stem, ext = p.stem, p.suffix
    counter = 1
    while True:
        candidate = Path(parent) / f"{stem} ({counter}){ext}"
        if not candidate.exists():
            return candidate
        counter += 1


def _safe_rename(filepath, target_name=None, target_dir=None):
    p = Path(filepath)
    final = unique_path(target_dir or p.parent, target_name or p.name)
    p.rename(final)
    return final


def rename_temp_files(out_dir):
    """Strip temp markers from finished downloads; auto-number collisions."""
    out = Path(out_dir)
    tmp_files = sorted(out.glob("*.__MGIDX_*__._MGTMP_.*")) + sorted(out.glob("*._MGTMP_.*"))
    seen = set()
    tmp_files = [t for t in tmp_files if not (t in seen or seen.add(t))]

    parsed = []
    for tmp in tmp_files:
        m = _TMP_RE.match(tmp.name)
        if m:
            parsed.append((tmp, m.group("title"), int(m.group("idx") or 0), m.group("ext")))
        else:
            parsed.append((tmp, tmp.name.replace("._MGTMP_.", ".", 1).rsplit(".", 1)[0],
                           0, tmp.suffix.lstrip(".")))

    renamed = []
    for tmp, title, idx, ext in sorted(parsed, key=lambda it: (it[1], it[2])):
        name = f"{title} - {idx:02d}.{ext}" if idx > 0 else f"{title}.{ext}"
        renamed.append(_safe_rename(tmp, target_name=name))
    return renamed


def _cleanup_temp(out_dir):
    for tmp in Path(out_dir).glob("*._MGTMP_.*"):
        try:
            tmp.unlink()
        except Exception:
            pass


# ── ERROR CLASSIFICATION ─────────────────────────────────────────────────────

def is_permanent_error(output_lines):
    combined = " ".join(output_lines[-10:]).lower()
    return any(p.lower() in combined for p in PERMANENT_ERRORS)


def is_no_video(output_lines):
    """yt-dlp found no video here - an image post, not a fault to retry."""
    text = "\n".join(output_lines).lower()
    return any(m in text for m in NO_VIDEO_MARKERS)


def is_tool_failure(output_lines):
    combined = " ".join(output_lines[-15:]).lower()
    return any(m in combined for m in TOOL_FAILURE_MARKERS)


# Markers meaning the gallery-dl binary itself cannot execute on this machine
# (glibc too old on Linux, wrong architecture on Intel Macs).
_ABI_MARKERS = ["glibc_", "glibc 2", "bad cpu type", "cannot execute binary file",
                "exec format error", "not found", "no such file or directory"]


def _looks_like_abi_failure(text):
    low = text.lower()
    return any(m in low for m in _ABI_MARKERS)


# ── yt-dlp ───────────────────────────────────────────────────────────────────

def build_ytdlp_args(url, cfg, resolution_override=None, out_dir_override=None):
    args = [str(YTDLP_EXE), "--ffmpeg-location", str(TOOLS_DIR)]
    if DENO_EXE.exists():
        args += ["--js-runtimes", f"deno:{DENO_EXE}"]

    out_dir = out_dir_override or cfg.get("output_dir", OUTPUT_DIR)
    args += ["-P", str(out_dir)]
    args += ["-o", "%(title)s.__MGIDX_%(playlist_index|0)s__._MGTMP_.%(ext)s"]
    args += ["--no-part"]

    carousel = is_carousel_candidate(url)
    if carousel:
        args += ["--yes-playlist"]

    if needs_login(url):
        args += cookie_args(cfg)

    # TikTok (Aug 2026): the default UA gets "Unexpected response from webpage
    # request"; a real browser UA + Referer fixes it (yt-dlp #17403).
    if re.search(r"tiktok\.com", url, re.IGNORECASE):
        args += ["--user-agent", BROWSER_UA,
                 "--add-headers", "Referer:https://www.tiktok.com/"]

    mode = cfg.get("mode", "video")
    if mode == "audio":
        args += ["-x", "--audio-format", cfg.get("audio_format", "mp3")]
    elif mode == "media" or carousel:
        # Media mode and carousels both mix images with video — a strict video
        # selector or --recode-video would fail on the image entries.
        args += ["-f", "best"]
    else:
        args += ["--recode-video", cfg.get("video_format", "mp4")]
        res = resolution_override or cfg.get("resolution", "best")
        if res == "best":
            args += ["-f", "bestvideo+bestaudio/best"]
        elif res == "worst":
            args += ["-f", "worstvideo+worstaudio/worst"]
        else:
            args += ["-f", f"bestvideo[height<={res}]+bestaudio/best[height<={res}]/best"]

    args += ["--retries", str(cfg.get("max_retries", 3)), "--no-mtime", "--newline"]
    args.append(url)
    return args


# ── gallery-dl ───────────────────────────────────────────────────────────────

def download_gallerydl(url, target_dir, cfg, tag, base_name=None, single=False,
                       rescue=False):
    """Download a post (images + videos) into target_dir via gallery-dl.

    ``rescue`` marks a speculative attempt on a URL that yt-dlp already failed:
    gallery-dl may have no extractor for the site at all, which is an expected
    miss rather than a fault, so it is reported quietly.

    Returns (success, n_new_files).
    """
    if not gallerydl_available():
        log(f"{tag} gallery-dl not installed — will fall back to yt-dlp", "WARN")
        return (False, 0)

    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    before = {f.name for f in target_dir.iterdir()}

    # -D forces a flat, exact target directory (no site/user nesting).
    args = gallerydl_command() + ["-D", str(target_dir),
                                  "--retries", str(cfg.get("max_retries", 3))]

    if re.search(r"tiktok\.com", url, re.IGNORECASE):
        args += ["--user-agent", BROWSER_UA]

    # Meaningful filenames instead of gallery-dl's numeric media IDs.
    if base_name:
        args += ["-f", (f"{base_name}.{{extension}}" if single
                        else f"{base_name} - {{num:>02}}.{{extension}}")]

    cargs = cookie_args(cfg)
    if cargs:
        args += cargs
    elif needs_login(url):
        log(f"{tag} No login cookies — this site may reject the request "
            f"(menu [11] / [10])", "WARN")

    args.append(url)
    log(f"  Target: {target_dir}", "INFO")

    collected = []
    skipped = []
    try:
        process = popen_stream(args)
        for line in process.stdout:
            if stop_requested():
                log(f"{tag} Stop requested — cancelling download...", "WARN")
                stop_process(process)
                raise DownloadStopped()
            line = line.rstrip()
            if not line:
                continue
            collected.append(line)
            # gallery-dl prefixes a path with "# " when the file is already on
            # disk and it skipped the download. That is a success — the media
            # is present — but nothing new appears in the folder, so without
            # this the run looks like a total miss.
            if line.lstrip().startswith("#"):
                skipped.append(line.lstrip()[1:].strip())
            low = line.lower()
            if "error" in low:
                log(f"  {line}", "INFO" if rescue else "ERROR")
            elif "warning" in low:
                log(f"  {line}", "WARN")
            else:
                log(f"  {line}", "INFO")
        process.wait()
    except DownloadStopped:
        raise
    except Exception as e:
        # Whatever went wrong reading the stream, files already on disk are
        # the truth. Losing a finished download to a logging hiccup would be
        # the worst outcome here.
        landed = [f for f in target_dir.iterdir()
                  if f.is_file() and f.name not in before]
        if landed:
            log(f"{tag} Could not read gallery-dl output ({e}), but "
                f"{len(landed)} file(s) arrived", "WARN")
            return (True, len(landed))

        # A binary that will not exec raises here (OSError/FileNotFoundError).
        if _looks_like_abi_failure(str(e)) and repair_gallerydl():
            log(f"{tag} Retrying with the repaired gallery-dl...", "WARN")
            return download_gallerydl(url, target_dir, cfg, tag, base_name, single)
        log(f"{tag} gallery-dl crashed: {e}", "INFO" if rescue else "ERROR")
        return (False, 0)

    new_files = [f for f in target_dir.iterdir()
                 if f.is_file() and f.name not in before]
    if process.returncode == 0 and new_files:
        return (True, len(new_files))

    if process.returncode == 0 and skipped:
        names = ", ".join(Path(f).name for f in skipped[:3])
        more = f" (+{len(skipped) - 3} more)" if len(skipped) > 3 else ""
        log(f"{tag} Already downloaded: {names}{more}", "OK")
        return (True, len(skipped))

    if not new_files and _looks_like_abi_failure("\n".join(collected[-5:])):
        if repair_gallerydl():
            log(f"{tag} Retrying with the repaired gallery-dl...", "WARN")
            return download_gallerydl(url, target_dir, cfg, tag, base_name, single)

    return (False, len(new_files))


# ── TikTok embed fallback ────────────────────────────────────────────────────

def download_tiktok_embed(url, out_dir, cfg, tag, base_name=None):
    """Last resort for TikTok (yt-dlp #17403): the official embed page still
    hands out signed tiktokcdn.com media URLs."""
    vid = tiktok_video_id(url)
    if not vid:
        return False
    log(f"{tag} Trying TikTok embed-page workaround...", "WARN")

    try:
        req = Request(f"https://www.tiktok.com/embed/v2/{vid}",
                      headers={"User-Agent": BROWSER_UA,
                               "Referer": "https://www.tiktok.com/"})
        page = urlopen(req, timeout=30).read().decode("utf-8", "replace")
    except Exception as e:
        log(f"  Embed page fetch failed: {e}", "ERROR")
        return False

    cands = [_html.unescape(c) for c in re.findall(r'https:[^"\']*tiktokcdn[^"\']*', page)]
    # Video streams live under /video/ paths; 'tplv' URLs are thumbnails.
    vurls = [c for c in cands if "/video/" in c and "tplv" not in c]
    if not vurls:
        log("  No video URL found in embed page (photo post or region block)", "ERROR")
        return False

    target = unique_path(Path(out_dir), f"{base_name or f'tiktok_{vid}'}.mp4")
    try:
        req = Request(vurls[0], headers={"User-Agent": BROWSER_UA,
                                         "Referer": "https://www.tiktok.com/"})
        with urlopen(req, timeout=180) as resp, open(target, "wb") as f:
            total_b = int(resp.headers.get("Content-Length", 0))
            done = 0
            while True:
                if stop_requested():
                    raise DownloadStopped()
                chunk = resp.read(1024 * 256)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total_b:
                    print(f"\r  {C.DIM}{done / 1048576:.1f}/{total_b / 1048576:.1f} MB"
                          f"{C.RESET}   {C.YELLOW}[Press Q to cancel]{C.RESET}",
                          end="", flush=True)
        print()
        if total_b and done < total_b:
            raise OSError(f"incomplete ({done}/{total_b} bytes)")
    except DownloadStopped:
        print()
        _unlink(target)
        raise
    except Exception as e:
        print()
        log(f"  Direct download failed: {e}", "ERROR")
        _unlink(target)
        return False

    log(f"  Saved: {target.name}", "OK")
    return True


def _unlink(path):
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass


# ── ORCHESTRATION ────────────────────────────────────────────────────────────

def _rescue(url, out_dir, cfg, tag, base_name, multi, gallerydl_tried,
            output_lines, reason):
    """Last resort after yt-dlp: gallery-dl, then TikTok's embed page.

    Reached two ways: yt-dlp ran out of retries, or it reported that the link
    holds no video at all. The second case is an image post — retrying that
    three times and force-updating the tools only wastes the user's time, so it
    comes straight here.

    ``gallerydl_tried`` avoids asking gallery-dl twice about the same URL when
    media mode already had a go at it.
    """
    if not multi:
        if not gallerydl_tried:
            log(f"{tag} Asking gallery-dl for this post...", "INFO")
            ok, _ = download_gallerydl(url, Path(out_dir), cfg, tag,
                                       base_name=base_name, single=True,
                                       rescue=True)
            if ok:
                log(f"{tag} Completed via gallery-dl: {url}", "OK")
                return (url, True, "OK (gallery-dl)")

        if re.search(r"tiktok\.com", url, re.IGNORECASE):
            if download_tiktok_embed(url, out_dir, cfg, tag, base_name=base_name):
                log(f"{tag} Completed via embed workaround: {url}", "OK")
                return (url, True, "OK (embed workaround)")

    log(f"{tag} {reason}: {url}", "ERROR")
    if output_lines:
        log("  Last output: " + "\n".join(output_lines[-5:]), "ERROR")
    return (url, False, reason)


def download_single(url, cfg, index, total, resolution_override=None):
    """Download one URL with smart retry. Returns (url, success, message)."""
    tag = f"[{index}/{total}]"
    max_attempts = cfg.get("max_retries", 3)
    out_dir = cfg.get("output_dir", OUTPUT_DIR)

    media_mode = cfg.get("mode", "video") == "media"

    # Self-heal: a missing tool triggers an immediate forced update.
    wants_gallerydl = media_mode or is_post_url(url)
    if not YTDLP_EXE.exists() or (wants_gallerydl and not gallerydl_available()):
        log(f"{tag} Required tool missing — updating tools now...", "WARN")
        run_updates(cfg, force=True)

    # Posts on a known platform are probed first, so the files are named from
    # the caption and a multi-item post gets its own folder. Media mode takes
    # the gallery-dl route for any URL, whatever the post turns out to hold.
    gallerydl_tried = False
    multi = False
    base_name = None
    if wants_gallerydl:
        log(f"{tag} Probing post metadata...", "INFO")
        count, caption = probe_post(url, cfg)
        base_name = post_base_name(url, caption, cfg)
        if count and count > 1:
            multi = True
            sub_dir = Path(out_dir) / base_name
            log(f"{tag} Post holds {count} items -> {base_name}", "OK")
            ok, n = download_gallerydl(url, sub_dir, cfg, tag, base_name=base_name)
            if ok:
                log(f"{tag} Completed: {n} file(s) -> {sub_dir.name}", "OK")
                return (url, True, f"OK ({n} files in {sub_dir.name})")
            log(f"{tag} gallery-dl failed — falling back to yt-dlp", "WARN")
            out_dir = str(sub_dir)
        elif media_mode:
            # One item, but in media mode it is still gallery-dl's job: the
            # item is often an image that yt-dlp cannot fetch at all.
            # On a known post an error is worth showing; on any other link
            # media mode is speculative, so a miss stays quiet.
            ok, n = download_gallerydl(url, Path(out_dir), cfg, tag,
                                       base_name=base_name, single=True,
                                       rescue=not is_post_url(url))
            gallerydl_tried = True
            if ok:
                log(f"{tag} Completed: {n} file(s)", "OK")
                return (url, True, f"OK ({n} file(s) via gallery-dl)")
            log(f"{tag} gallery-dl got nothing — trying yt-dlp", "WARN")
        else:
            log(f"{tag} Single post — downloading normally", "INFO")

    attempt = 0
    updated_once = False
    while attempt < max_attempts:
        attempt += 1
        if attempt == 1:
            log(f"{tag} Starting: {url}", "DOWNLOAD")
        else:
            log(f"{tag} Retry {attempt - 1}/{max_attempts - 1}...", "WARN")

        args = build_ytdlp_args(url, cfg, resolution_override,
                                out_dir_override=out_dir if multi else None)
        try:
            process = popen_stream(args)
            output_lines = []
            for line in process.stdout:
                if stop_requested():
                    print()
                    log(f"{tag} Stop requested — cancelling download...", "WARN")
                    stop_process(process)
                    raise DownloadStopped()
                line = line.rstrip()
                if not line:
                    continue
                output_lines.append(line)

                if "[download]" in line and "%" in line:
                    print(f"\r  {C.DIM}{line.strip()}{C.RESET}   "
                          f"{C.YELLOW}[Press Q to cancel]{C.RESET}", end="", flush=True)
                elif "[download]" in line and "Destination" in line:
                    print()
                    log(f"  {line.strip()}", "INFO")
                elif any(kw in line for kw in ("[Merger]", "[ExtractAudio]",
                                               "[VideoConvertor]", "[ffmpeg]",
                                               "[VideoRemuxer]")):
                    log(f"  {line.strip()}", "INFO")
                elif "ERROR" in line.upper():
                    log(f"  {line.strip()}", "ERROR")

            print()
            process.wait()

            if process.returncode == 0:
                for fp in rename_temp_files(out_dir):
                    log(f"  Saved: {fp.name}", "OK")
                log(f"{tag} Completed: {url}", "OK")
                return (url, True, "OK")

            _cleanup_temp(out_dir)

            if is_permanent_error(output_lines):
                log(f"{tag} Permanent failure (no retry): {url}", "ERROR")
                log("  " + "\n".join(output_lines[-3:]), "ERROR")
                return (url, False, "Permanent error — video unavailable/private/blocked")

            # No video at all: an image post. Retrying and force-updating
            # the tools cannot conjure one, so hand it over immediately.
            if is_no_video(output_lines):
                log(f"{tag} yt-dlp found no video here - treating it as an "
                    f"image post", "INFO")
                return _rescue(url, out_dir, cfg, tag, base_name, multi,
                               gallerydl_tried, output_lines,
                               "No video in this post")

            # Outdated-tool error: force-update once and retry for free.
            if not updated_once and is_tool_failure(output_lines):
                log(f"{tag} Failure looks tool-related — force-updating tools and retrying...", "WARN")
                run_updates(cfg, force=True)
                updated_once = True
                attempt -= 1
                continue

            if attempt < max_attempts:
                time.sleep(3 * attempt)
                continue

            return _rescue(url, out_dir, cfg, tag, base_name, multi,
                           gallerydl_tried, output_lines,
                           f"Failed after {max_attempts} attempts")

        except DownloadStopped:
            _cleanup_temp(out_dir)
            raise

        except FileNotFoundError:
            log(f"{tag} Downloader binary missing — reinstalling tools...", "ERROR")
            run_updates(cfg, force=True)
            continue

        except OSError as e:
            # "Bad CPU type" / "Exec format error": wrong-architecture binary.
            if _looks_like_abi_failure(str(e)):
                log(f"{tag} A bundled tool will not run on this machine ({e}) — "
                    "re-downloading for this platform...", "ERROR")
                run_updates(cfg, force=True)
                continue
            log(f"{tag} Crashed: {url} — {e}", "ERROR")
            return (url, False, str(e))

        except Exception as e:
            log(f"{tag} Crashed: {url} — {e}", "ERROR")
            return (url, False, str(e))

    return (url, False, "Unknown failure")
