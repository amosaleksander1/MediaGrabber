#!/usr/bin/env python3
"""Platform matrix check.

MediaGrabber has to pick the right download URL, profile path and OS helper
for four targets, but CI can only really execute one of them. This test fakes
``sys.platform``/``platform.machine`` before importing the package and asserts
the resolved values, so a wrong macOS asset name fails here instead of on a
user's Mac.

Run:  python3 tests/test_platform_matrix.py
"""


import os

import subprocess
import sys

# This test prints box-drawing characters and is meant to run from any host OS,
# including a Windows console whose default encoding is cp1252.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover — exotic stream
        pass

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CHILD = r'''
import platform, sys, types, os
# Several stdlib modules branch on sys.platform at *import* time (urllib.request
# pulls in the macOS-only _scproxy; subprocess pulls in _winapi). Import them
# for real before we start lying about the platform.
import urllib.request, subprocess, socket, shutil  # noqa: F401

sys.path.insert(0, {repo!r})
sys.platform = {plat!r}
platform.machine = lambda: {mach!r}
platform.mac_ver = lambda: ({macver!r}, ("", "", ""), "")

if {plat!r} == "win32":
    msvcrt = types.ModuleType("msvcrt")
    msvcrt.kbhit = lambda: False
    msvcrt.getwch = lambda: ""
    sys.modules["msvcrt"] = msvcrt
    subprocess.CREATE_NO_WINDOW = 0x08000000
    os.environ.setdefault("LOCALAPPDATA", r"C:\Users\test\AppData\Local")
    os.environ.setdefault("APPDATA", r"C:\Users\test\AppData\Roaming")

from mediagrabber import platform_support as ps
from mediagrabber import tools, cookies
import json

print(json.dumps({{
    "tag": ps.PLATFORM_TAG,
    "arch": ps.ARCH,
    "os": ps.OS_LABEL,
    "exe_suffix": ps.EXE,
    "ytdlp_asset": tools.ytdlp_asset_name(),
    "ytdlp_url": tools.ytdlp_download_url(),
    "gallerydl_asset": tools.gallerydl_asset_name(),
    "deno_asset": tools.deno_asset_name(),
    "ffmpeg_macos": tools.ffmpeg_macos_urls() if ps.IS_MAC else None,
    "browsers": cookies.browser_order(),
    "chrome_profile": str(cookies.browser_profile_paths()["chrome"]),
    "macos_version": ps.macos_version(),
}}))
'''

CASES = {
    "macOS arm64": ("darwin", "arm64", "15.3"),
    "macOS x86_64": ("darwin", "x86_64", "14.7"),
    "Windows x86_64": ("win32", "AMD64", ""),
    "Linux x86_64": ("linux", "x86_64", ""),
    "Linux arm64": ("linux", "aarch64", ""),
}

EXPECT = {
    "macOS arm64": {
        "tag": "macos-arm64",
        "ytdlp_asset": "yt-dlp_macos",
        "gallerydl_asset": "gallery-dl_macos",
        "deno_asset": "deno-aarch64-apple-darwin.zip",
        "exe_suffix": "",
    },
    "macOS x86_64": {
        "tag": "macos-x64",
        "ytdlp_asset": "yt-dlp_macos",
        # No published Intel-macOS binary -> pip fallback path.
        "gallerydl_asset": None,
        "deno_asset": "deno-x86_64-apple-darwin.zip",
        "exe_suffix": "",
    },
    "Windows x86_64": {
        "tag": "windows-x64",
        "ytdlp_asset": "yt-dlp.exe",
        "gallerydl_asset": "gallery-dl_windows.exe",
        "deno_asset": "deno-x86_64-pc-windows-msvc.zip",
        "exe_suffix": ".exe",
    },
    "Linux x86_64": {
        "tag": "linux-x64",
        "ytdlp_asset": "yt-dlp",
        "gallerydl_asset": "gallery-dl_linux",
        "deno_asset": "deno-x86_64-unknown-linux-gnu.zip",
        "exe_suffix": "",
    },
    "Linux arm64": {
        "tag": "linux-arm64",
        "ytdlp_asset": "yt-dlp_linux_aarch64",
        "gallerydl_asset": "gallery-dl_linux",
        "deno_asset": "deno-aarch64-unknown-linux-gnu.zip",
        "exe_suffix": "",
    },
}


def run_case(plat, mach, macver):
    import json
    code = CHILD.format(repo=REPO, plat=plat, mach=mach, macver=macver)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError(f"child failed:\n{r.stderr}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def main():
    failures = []
    for name, (plat, mach, macver) in CASES.items():
        got = run_case(plat, mach, macver)
        print(f"\n── {name} ──")
        for k in ("tag", "arch", "exe_suffix", "ytdlp_asset", "gallerydl_asset",
                  "deno_asset"):
            print(f"   {k:<16} {got[k]}")
        if got["ffmpeg_macos"]:
            for u in got["ffmpeg_macos"]:
                print(f"   ffmpeg           {u}")
        print(f"   browsers         {', '.join(got['browsers'])}")
        print(f"   chrome profile   {got['chrome_profile']}")

        for k, want in EXPECT[name].items():
            if got[k] != want:
                failures.append(f"{name}: {k} = {got[k]!r}, expected {want!r}")

        # macOS-specific invariants.
        if plat == "darwin":
            # The child builds paths with the *host's* pathlib flavour, so on a
            # Windows host the separators come back as backslashes. Compare on
            # a normalised form — the path shape is what matters here.
            profile = got["chrome_profile"].replace("\\", "/")
            if "safari" not in got["browsers"]:
                failures.append(f"{name}: Safari missing from browser list")
            if "Library/Application Support" not in profile:
                failures.append(f"{name}: Chrome profile path is not a macOS path")
            slug = "arm64" if mach == "arm64" else "amd64"
            if f"/macos/{slug}/" not in got["ffmpeg_macos"][0]:
                failures.append(f"{name}: ffmpeg URL has wrong arch slug")
            if not got["ffmpeg_macos"][1].endswith("ffprobe.zip"):
                failures.append(f"{name}: ffprobe URL wrong")
        else:
            if "safari" in got["browsers"]:
                failures.append(f"{name}: Safari offered on a non-macOS platform")

    print("\n" + "=" * 60)
    if failures:
        print("FAILURES:")
        for f in failures:
            print("  ✗ " + f)
        return 1
    print(f"All {len(CASES)} platform cases resolved correctly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
