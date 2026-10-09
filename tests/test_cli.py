#!/usr/bin/env python3
"""The command line an AI agent or a script calls.

What an agent relies on, and so what this pins down:

  * --json puts exactly one parseable object on stdout;
  * the exit code tells success (0), failure (1) and bad arguments (2) apart;
  * config set validates instead of writing garbage the menu then chokes on;
  * download overrides never leak into the saved settings;
  * queue adds a link once, however often it is sent.

Everything runs against a throwaway config and urls.txt, never the real ones,
and nothing here touches the network.

Run:  python3 tests/test_cli.py
"""

import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover — exotic stream
        pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mediagrabber import bridge, cli, config               # noqa: E402


def run(*argv):
    """(exit code, stdout, stderr) of one CLI call."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(list(argv))
        except SystemExit as e:                 # argparse usage / --version
            code = e.code
    return code, out.getvalue(), err.getvalue()


def run_json(fail, *argv):
    code, out, _ = run(*argv, "--json")
    try:
        return code, json.loads(out)
    except ValueError:
        fail(f"{' '.join(argv)} --json printed non-JSON on stdout: {out[:200]!r}")
        return code, {}


def check(fail, tmp):
    code, out, _ = run("--version")
    if code != 0 or "MediaGrabber" not in out:
        fail(f"--version gave code {code}, output {out!r}")

    code, got = run_json(fail, "config", "get")
    if code != 0 or got.get("config", {}).get("mode") != "video":
        fail(f"config get gave code {code}: {got}")

    code, got = run_json(fail, "config", "set", "mode", "audio")
    if code != 0 or got.get("value") != "audio":
        fail(f"config set mode audio gave code {code}: {got}")
    if json.loads(config.CONFIG_FILE.read_text())["mode"] != "audio":
        fail("config set must persist the value")

    code, got = run_json(fail, "config", "set", "resolution", "720p")
    if got.get("value") != "720":
        fail(f"720p should be stored as 720, got {got}")
    code, got = run_json(fail, "config", "set", "auto_update", "off")
    if got.get("value") is not False:
        fail(f"auto_update off should be stored as false, got {got}")

    for bad in (("mode", "banana"), ("resolution", "999"), ("nope", "1"),
                ("max_retries", "zero")):
        code, got = run_json(fail, "config", "set", *bad)
        if code != 2 or got.get("ok") is not False:
            fail(f"config set {bad} must be refused with code 2, got {code}")
    if json.loads(config.CONFIG_FILE.read_text())["mode"] != "audio":
        fail("a refused value must not change the saved config")

    code, _, _ = run("nonsense")
    if code != 2:
        fail(f"an unknown command must exit 2, got {code}")

    # Overrides belong to one run: --format mp3 means audio for this download,
    # and the config handed in comes back untouched.
    saved = dict(config.DEFAULTS)
    args = cli.build_parser().parse_args(
        ["download", "u", "--format", "mp3", "--resolution", "1080p",
         "--out", str(tmp / "out")])
    run_cfg = cli._overrides(saved, args)
    if (run_cfg["mode"], run_cfg["audio_format"]) != ("audio", "mp3"):
        fail(f"--format mp3 should switch this run to audio: {run_cfg}")
    if run_cfg["resolution"] != "1080" or not run_cfg["output_dir"].endswith("out"):
        fail(f"--resolution/--out not applied: {run_cfg}")
    if saved != config.DEFAULTS:
        fail("download overrides must not touch the saved config")

    code, got = run_json(fail, "download", "u", "--format", "mp3", "--video")
    if code != 2:
        fail(f"--video with an audio format must exit 2, got {code}")

    url = "https://www.instagram.com/p/ABCdef123/"
    code, got = run_json(fail, "queue", url)
    if code != 0 or not got["results"][0].get("queued"):
        fail(f"queue gave code {code}: {got}")
    code, got = run_json(fail, "queue", url)
    if got["results"][0].get("queued"):
        fail("the same link must not be queued twice")
    if bridge.URLS_FILE.read_text().count(url) != 1:
        fail("urls.txt should hold the link exactly once")
    code, got = run_json(fail, "queue", "not a url")
    if code != 1:
        fail(f"queueing a non-URL must exit 1, got {code}")


def main():
    failures = []
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        config.CONFIG_FILE = tmp / "config.json"
        cli.URLS_FILE = bridge.URLS_FILE = tmp / "urls.txt"
        cli.LOGS_DIR = tmp / "logs"
        check(failures.append, tmp)

    print("Checked --version, config get/set with validation, exit codes, "
          "per-run download overrides and queue de-duplication.")
    print("=" * 60)
    if failures:
        print("FAILURES:")
        for f in failures:
            print("  x " + f)
        return 1
    print("The command line behaves correctly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
