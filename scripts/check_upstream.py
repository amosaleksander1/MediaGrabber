#!/usr/bin/env python3
"""Verify that every upstream binary MediaGrabber depends on still exists.

MediaGrabber downloads yt-dlp, ffmpeg, ffprobe, Deno and gallery-dl at runtime
from third-party release feeds. Those feeds change without warning: an asset
gets renamed, a platform stops being published (there is already no Intel-macOS
gallery-dl build), or a project moves repos. When that happens the app breaks
on a user's machine, silently, on a platform the maintainer may not own.

This script is the early-warning system. For every supported platform it asks
the package which URL it *would* download, then checks that URL actually
resolves — so a broken macOS asset surfaces in CI rather than in an issue
report. It also records the current upstream versions so the diff in the
resulting pull request shows what moved.

Exit codes:
    0  every URL resolves
    1  at least one URL is broken (CI opens an issue)
"""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STATE_FILE = REPO / ".github" / "upstream-versions.json"

UA = {"User-Agent": "MediaGrabber-upstream-check"}

# (label, sys.platform, machine)
TARGETS = [
    ("windows-x64", "win32", "AMD64"),
    ("macos-arm64", "darwin", "arm64"),
    ("macos-x64", "darwin", "x86_64"),
    ("linux-x64", "linux", "x86_64"),
    ("linux-arm64", "linux", "aarch64"),
]

_CHILD = r'''
import platform, sys, types, os
import urllib.request, subprocess, socket, shutil  # import before faking platform
sys.path.insert(0, {repo!r})
sys.platform = {plat!r}
platform.machine = lambda: {mach!r}
platform.mac_ver = lambda: ("14.0", ("", "", ""), "")
if {plat!r} == "win32":
    m = types.ModuleType("msvcrt"); m.kbhit = lambda: False; m.getwch = lambda: ""
    sys.modules["msvcrt"] = m
    subprocess.CREATE_NO_WINDOW = 0x08000000
    os.environ.setdefault("LOCALAPPDATA", "C:\\x"); os.environ.setdefault("APPDATA", "C:\\y")

from mediagrabber import tools
import json

urls = {{"yt-dlp": tools.ytdlp_download_url()}}

gdl = tools.gallerydl_asset_name()
urls["gallery-dl"] = (
    "https://github.com/gdl-org/builds/releases/latest/download/" + gdl
    if gdl else None)

urls["deno"] = ("https://github.com/denoland/deno/releases/latest/download/"
                + tools.deno_asset_name())

if {plat!r} == "darwin":
    f, p = tools.ffmpeg_macos_urls()
    urls["ffmpeg"] = f
    urls["ffprobe"] = p
else:
    urls["ffmpeg"] = "GITHUB_API:BtbN/FFmpeg-Builds"

print(json.dumps(urls))
'''


def resolved_urls(plat, mach):
    code = _CHILD.format(repo=str(REPO), plat=plat, mach=mach)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"Could not resolve URLs for {plat}/{mach}:\n{r.stderr}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def url_ok(url):
    """True if the URL resolves. Uses a 1-byte ranged GET: HEAD is unreliable
    on both GitHub release redirects and the ffmpeg build server."""
    req = urllib.request.Request(url, headers={**UA, "Range": "bytes=0-0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return 200 <= resp.status < 400
    except urllib.error.HTTPError as e:
        return 200 <= e.code < 400
    except Exception:
        return False


def gh_latest(repo):
    """Latest release tag for a GitHub repo (token used when available)."""
    headers = dict(UA)
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/releases/latest", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
        return data.get("tag_name") or data.get("published_at") or "unknown"
    except Exception as e:
        return f"error: {e}"


def ffmpeg_macos_build(slug):
    """The versioned path segment the macOS ffmpeg 'latest' redirect lands on."""
    url = f"https://ffmpeg.martin-riedl.de/redirect/latest/macos/{slug}/release/ffmpeg.zip"
    try:
        req = urllib.request.Request(url, headers={**UA, "Range": "bytes=0-0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.geturl().rstrip("/").split("/")[-2]
    except Exception as e:
        return f"error: {e}"


def main():
    print(f"Checking upstream binaries — {datetime.now(timezone.utc).isoformat()}\n")

    broken = []
    checked = 0

    for label, plat, mach in TARGETS:
        print(f"── {label} ──")
        for tool, url in sorted(resolved_urls(plat, mach).items()):
            if url is None:
                print(f"   {tool:<11} (no published binary — pip fallback)")
                continue
            if url.startswith("GITHUB_API:"):
                print(f"   {tool:<11} resolved from the {url.split(':', 1)[1]} API at runtime")
                continue
            ok = url_ok(url)
            checked += 1
            print(f"   {tool:<11} {'OK  ' if ok else 'DEAD'} {url}")
            if not ok:
                broken.append(f"{label} / {tool}: {url}")
        print()

    state = {
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "upstream": {
            "yt-dlp": gh_latest("yt-dlp/yt-dlp"),
            "gallery-dl": gh_latest("gdl-org/builds"),
            "deno": gh_latest("denoland/deno"),
            "ffmpeg-btbn": gh_latest("BtbN/FFmpeg-Builds"),
            "ffmpeg-macos-arm64": ffmpeg_macos_build("arm64"),
            "ffmpeg-macos-x64": ffmpeg_macos_build("amd64"),
        },
    }

    print("Upstream versions:")
    for k, v in state["upstream"].items():
        print(f"   {k:<20} {v}")

    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    print(f"\nWrote {STATE_FILE.relative_to(REPO)}")

    print(f"\n{checked} URL(s) checked.")
    if broken:
        print("\nBROKEN UPSTREAM ASSETS:")
        for b in broken:
            print("  ✗ " + b)
        # Surface the list to the workflow so it can file an issue.
        summary = os.environ.get("GITHUB_OUTPUT")
        if summary:
            with open(summary, "a", encoding="utf-8") as f:
                f.write("broken<<EOF\n" + "\n".join(broken) + "\nEOF\n")
        return 1

    print("All upstream assets resolve.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
