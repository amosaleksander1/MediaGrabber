"""The command line: MediaGrabber as a tool a script or an AI agent can call.

The menu is built for a person at a keyboard. An agent asked to "download this
for me" needs the opposite: one command, no questions, a result it can parse
and an exit code it can trust. So every command here

* never prompts — ``ui.NO_PROMPTS`` turns each question into its no-answer
  path, because an agent cannot see a prompt and its stdin may still be a TTY;
* with ``--json``, prints exactly one JSON object on stdout and sends all the
  progress chatter to stderr;
* exits 0 when everything worked, 1 when anything failed, 2 on bad arguments.

Overrides given to ``download`` apply to that run only; the saved settings the
person uses in the menu are changed only by ``config set``.
"""

import argparse
import contextlib
import json
import sys
from pathlib import Path

from . import APP_VERSION, ui
from .config import (AUDIO_FORMATS, LOGS_DIR, OUTPUT_DIR, RESOLUTION_OPTIONS,
                     URLS_FILE, VIDEO_FORMATS, load_config, save_config)

_VIDEO = [v for v, _ in VIDEO_FORMATS]
_AUDIO = [v for v, _ in AUDIO_FORMATS]
_RESOLUTIONS = [v for v, _ in RESOLUTION_OPTIONS]

EXAMPLES = f"""\
examples:
  MediaGrabber download "https://youtu.be/dQw4w9WgXcQ" --json
  MediaGrabber download URL --audio --format mp3 --out ~/Music
  MediaGrabber download URL --resolution 720
  MediaGrabber queue URL1 URL2          add links to urls.txt
  MediaGrabber batch --json             download everything in urls.txt
  MediaGrabber config get --json
  MediaGrabber config set mode audio
  MediaGrabber checkup --json
  MediaGrabber update

video formats: {', '.join(_VIDEO)}
audio formats: {', '.join(_AUDIO)}
resolutions:   {', '.join(_RESOLUTIONS)}

--json prints one object on stdout: {{"ok": true|false, ...}}; download and
batch add "results": [{{"url", "ok", "message", "files"}}]. Progress goes to
stderr. Exit code: 0 all succeeded, 1 something failed, 2 bad arguments
(an unparseable command line exits 2 with stdout empty and the reason on
stderr).
Run with no arguments for the interactive menu.
"""


class UsageError(Exception):
    """Bad input from the caller: exit 2, like argparse's own errors."""


# ── SETTINGS ─────────────────────────────────────────────────────────────────

def _settable():
    from .cookies import browser_order
    return {
        "mode": ["video", "audio"],
        "video_format": _VIDEO,
        "audio_format": _AUDIO,
        "resolution": _RESOLUTIONS,
        "auto_update": bool,
        "cookies_browser": list(browser_order()) + ["auto", "none"],
        "output_dir": Path,
        "max_retries": int,
        "folder_name_words": int,
    }


def coerce(key, raw):
    """Turn a typed value into what config.json holds, or raise UsageError."""
    kinds = _settable()
    if key not in kinds:
        raise UsageError(f"unknown setting {key!r}; one of: {', '.join(kinds)}")
    kind = kinds[key]
    text = str(raw).strip()
    if kind is bool:
        low = text.lower()
        if low in ("true", "on", "yes", "1"):
            return True
        if low in ("false", "off", "no", "0"):
            return False
        raise UsageError(f"{key} must be on or off")
    if kind is int:
        try:
            value = int(text)
        except ValueError:
            raise UsageError(f"{key} must be a whole number") from None
        if value < 1:
            raise UsageError(f"{key} must be 1 or more")
        return value
    if kind is Path:
        from .platform_support import normalise_pasted_path
        path = normalise_pasted_path(text)
        if path is None:
            raise UsageError("output_dir must be a folder path")
        return str(path.resolve())
    low = text.lower()
    if key == "resolution" and low.endswith("p") and low[:-1].isdigit():
        low = low[:-1]                      # "1080p" means 1080
    if low not in kind:
        raise UsageError(f"{key} must be one of: {', '.join(kind)}")
    return low


def _overrides(cfg, args):
    """A copy of cfg with this run's --audio/--format/... applied."""
    run = dict(cfg)
    if args.audio and args.video:
        raise UsageError("choose --audio or --video, not both")
    if args.audio:
        run["mode"] = "audio"
    if args.video:
        run["mode"] = "video"
    if args.format:
        fmt = args.format.lower()
        # A format names its mode, so --format mp3 alone means audio.
        if fmt in _AUDIO and not args.video:
            run["mode"], run["audio_format"] = "audio", fmt
        elif fmt in _VIDEO and not args.audio:
            run["mode"], run["video_format"] = "video", fmt
        else:
            raise UsageError(f"format {fmt!r} does not fit {run['mode']} mode; "
                             f"video: {', '.join(_VIDEO)}; "
                             f"audio: {', '.join(_AUDIO)}")
    if args.resolution:
        run["resolution"] = coerce("resolution", args.resolution)
    if args.out:
        run["output_dir"] = coerce("output_dir", args.out)
    return run


# ── COMMANDS ─────────────────────────────────────────────────────────────────

def _files(folder):
    # ponytail: rglob diff, slow on a huge output folder; have download_single
    # return the paths it wrote if that ever bites.
    root = Path(folder)
    if not root.exists():
        return set()
    return {p for p in root.rglob("*") if p.is_file()}


def _download(urls, cfg):
    """Download each URL, noting which new files it produced."""
    from .download import DownloadStopped, download_single
    from .tools import run_updates

    out = cfg.get("output_dir", OUTPUT_DIR)
    Path(out).mkdir(parents=True, exist_ok=True)
    # Instant when the tools are there; on a fresh install it fetches them.
    run_updates(cfg, interactive=False)

    results = []
    for i, url in enumerate(urls, 1):
        before = _files(out)
        try:
            _, ok, message = download_single(url, cfg, i, len(urls))
        except DownloadStopped:
            ok, message = False, "stopped"
        new = sorted(str(p) for p in _files(out) - before)
        results.append({"url": url, "ok": ok, "message": message,
                        "files": new})
        if message == "stopped":
            break
    return results


def cmd_download(args):
    cfg = _overrides(load_config(), args)
    results = _download(args.urls, cfg)
    return {"ok": all(r["ok"] for r in results), "results": results,
            "output_dir": cfg.get("output_dir", OUTPUT_DIR)}


def cmd_batch(args):
    from .app import clear_urls, read_urls
    cfg = load_config()
    urls = read_urls()
    if not urls:
        return {"ok": False, "error": f"no links in {URLS_FILE}",
                "results": []}
    results = _download(urls, cfg)
    ok = all(r["ok"] for r in results) and len(results) == len(urls)
    if ok:
        clear_urls()
    return {"ok": ok, "results": results,
            "output_dir": cfg.get("output_dir", OUTPUT_DIR)}


def cmd_queue(args):
    from .bridge import handle_queue
    results = [handle_queue({"url": url}) for url in args.urls]
    return {"ok": all(r.get("ok") for r in results), "results": results,
            "urls_file": str(URLS_FILE)}


def cmd_config(args):
    cfg = load_config()
    if args.action == "set":
        if args.key is None or args.value is None:
            raise UsageError("usage: config set KEY VALUE")
        value = coerce(args.key, args.value)
        if args.key == "output_dir":
            Path(value).mkdir(parents=True, exist_ok=True)
        if args.key == "cookies_browser" and value != cfg.get(args.key):
            # The saved login belongs to the old browser; without dropping
            # it, downloads would quietly keep using that one. It is
            # re-exported from the new browser when next needed.
            from .config import COOKIES_FILE
            COOKIES_FILE.unlink(missing_ok=True)
        cfg[args.key] = value
        save_config(cfg)
        return {"ok": True, "key": args.key, "value": value}
    if args.key:
        if args.key not in cfg:
            raise UsageError(f"unknown setting {args.key!r}")
        return {"ok": True, "key": args.key, "value": cfg[args.key]}
    return {"ok": True, "config": cfg, "urls_file": str(URLS_FILE)}


def cmd_checkup(args):
    from .checkup import run_checkup
    from .tools import tools_present
    ok = run_checkup(load_config())
    return {"ok": bool(ok), "tools_present": tools_present(),
            "log_file": str(ui.log_file())}


def cmd_update(args):
    from .tools import run_updates
    ok = run_updates(load_config(), force=True, interactive=False)
    return {"ok": bool(ok), "log_file": str(ui.log_file())}


# ── ENTRY ────────────────────────────────────────────────────────────────────

def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true",
                        help="print one JSON result on stdout, progress on stderr")

    parser = argparse.ArgumentParser(
        prog="MediaGrabber",
        description="Download video, audio and images from a link. "
                    "Without arguments, opens the interactive menu.",
        epilog=EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version",
                        version=f"MediaGrabber {APP_VERSION}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    p = sub.add_parser("download", parents=[common],
                       help="download one or more links now")
    p.add_argument("urls", nargs="+", metavar="URL")
    p.add_argument("--audio", action="store_true", help="audio only, this run")
    p.add_argument("--video", action="store_true", help="video / image, this run")
    p.add_argument("--format", help="file format, e.g. mp4 or mp3 (this run)")
    p.add_argument("--resolution", help="best, worst or a height like 1080 (this run)")
    p.add_argument("--out", help="folder to save into (this run)")
    p.set_defaults(func=cmd_download)

    p = sub.add_parser("batch", parents=[common],
                       help="download every link in urls.txt")
    p.set_defaults(func=cmd_batch)

    p = sub.add_parser("queue", parents=[common],
                       help="add links to urls.txt for a later batch")
    p.add_argument("urls", nargs="+", metavar="URL")
    p.set_defaults(func=cmd_queue)

    p = sub.add_parser("config", parents=[common],
                       help="show or change saved settings")
    p.add_argument("action", choices=("get", "set"))
    p.add_argument("key", nargs="?")
    p.add_argument("value", nargs="?")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("checkup", parents=[common],
                       help="check the tools and the browser login")
    p.set_defaults(func=cmd_checkup)

    p = sub.add_parser("update", parents=[common],
                       help="update yt-dlp, ffmpeg, deno and gallery-dl now")
    p.set_defaults(func=cmd_update)
    return parser


def _human(result):
    """The same result for a person reading the terminal."""
    for r in result.get("results", []):
        if "message" in r:
            print(f"{'OK  ' if r['ok'] else 'FAIL'} {r['url']} — {r['message']}")
            for f in r.get("files", []):
                print(f"     {f}")
        else:
            print(json.dumps(r, ensure_ascii=False))
    if "error" in result:
        print(f"error: {result['error']}", file=sys.stderr)
    if "key" in result:
        print(f"{result['key']} = {result['value']}")
    if "config" in result:
        for key, value in result["config"].items():
            print(f"{key} = {value}")


def main(argv):
    from .platform_support import enable_ansi
    enable_ansi()
    args = build_parser().parse_args(argv)
    ui.NO_PROMPTS = True
    ui.init_logging(LOGS_DIR)

    code = 0
    try:
        if args.json:
            # Everything the work prints — log lines, progress bars — goes to
            # stderr, so stdout holds nothing but the one JSON object.
            with contextlib.redirect_stdout(sys.stderr):
                result = args.func(args)
        else:
            result = args.func(args)
    except UsageError as e:
        result, code = {"ok": False, "error": str(e)}, 2
    except KeyboardInterrupt:
        result, code = {"ok": False, "error": "interrupted"}, 1
    except Exception as e:
        result, code = {"ok": False, "error": f"{type(e).__name__}: {e}"}, 1

    if code == 0 and not result.get("ok"):
        code = 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, default=str))
    else:
        _human(result)
    return code
