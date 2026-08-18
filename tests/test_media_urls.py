#!/usr/bin/env python3
"""Post-URL recognition, naming, and per-mode yt-dlp arguments.

Media mode has to pull every image and video out of a post, but it must not
change what happens to ordinary video links. Two properties matter enough to
guard here, because both fail silently:

  * The multi-item predicate stays narrow. It gates ``--yes-playlist`` and the
    ``-f best`` selector, so if an X or Reddit video post ever starts matching
    it, those downloads quietly lose ``--recode-video`` and the user's
    resolution preference.
  * Post patterns match post-shaped paths only. A profile, board or subreddit
    root matching would turn one link into a mass download.

Run:  python3 tests/test_media_urls.py
"""

import os
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover — exotic stream
        pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mediagrabber.config import DEFAULTS                       # noqa: E402
from mediagrabber.download import (build_ytdlp_args,           # noqa: E402
                                   is_no_video, is_permanent_error,
                                   is_tool_failure)
from mediagrabber.probe import (is_carousel_candidate,         # noqa: E402
                                is_post_url, needs_login,
                                post_folder_name)

# url -> (is_post, is_multi_item, expected name)
POSTS = {
    "https://www.instagram.com/p/Cabc123_x/":
        (True, True, "instagram_Cabc123_x"),
    "https://www.instagram.com/reel/Cxyz789/":
        (True, True, "instagram_Cxyz789"),
    "https://www.tiktok.com/@someone/photo/7291234567":
        (True, True, "tiktok_someone_7291234567"),
    "https://www.tiktok.com/@someone/video/7291234567":
        (True, False, "tiktok_someone_7291234567"),
    "https://x.com/nasa/status/1889912345678":
        (True, False, "twitter_nasa_1889912345678"),
    "https://twitter.com/nasa/status/1889912345678":
        (True, False, "twitter_nasa_1889912345678"),
    "https://www.reddit.com/r/pics/comments/1abc2d/a_title/":
        (True, False, "reddit_pics_1abc2d"),
    "https://redd.it/1abc2d":
        (True, False, "reddit_1abc2d"),
    "https://www.pinterest.com/pin/123456789012/":
        (True, False, "pinterest_123456789012"),
    "https://pin.it/aBc12Xy":
        (True, False, "pinterest_aBc12Xy"),
    "https://www.threads.net/@someone/post/C1a2b3c":
        (True, False, "threads_someone_C1a2b3c"),
}

# Links that must NOT be treated as posts: profile/board/subreddit roots would
# become mass downloads, and "dropbox.com" contains the substring "x.com".
NON_POSTS = [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://vimeo.com/123456",
    "https://www.instagram.com/someone/",
    "https://www.reddit.com/r/pics/",
    "https://x.com/nasa",
    "https://www.pinterest.com/someone/a-board/",
    "https://www.dropbox.com/s/abc/status/123",
]

# Sites that need a session. Must stay in step with cookies.LOGIN_DOMAINS.
LOGIN_URLS = [
    "https://www.instagram.com/p/Cabc123_x/",
    "https://www.tiktok.com/@someone/video/7291234567",
    "https://x.com/nasa/status/1889912345678",
    "https://www.threads.net/@someone/post/C1a2b3c",
]
NO_LOGIN_URLS = [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://www.dropbox.com/home",
]


def _cfg(mode, resolution="1080"):
    cfg = dict(DEFAULTS)
    cfg["mode"] = mode
    cfg["resolution"] = resolution
    return cfg


def check_urls(fail):
    for url, (want_post, want_multi, want_name) in POSTS.items():
        if is_post_url(url) != want_post:
            fail(f"{url}: is_post_url != {want_post}")
        if is_carousel_candidate(url) != want_multi:
            fail(f"{url}: multi-item predicate != {want_multi}")
        got = post_folder_name(url)
        if got != want_name:
            fail(f"{url}: name {got!r}, expected {want_name!r}")

    for url in NON_POSTS:
        if is_post_url(url):
            fail(f"{url}: matched as a post but must not")
        if is_carousel_candidate(url):
            fail(f"{url}: matched as multi-item but must not")

    for url in LOGIN_URLS:
        if not needs_login(url):
            fail(f"{url}: should need login cookies")
    for url in NO_LOGIN_URLS:
        if needs_login(url):
            fail(f"{url}: should not need login cookies")


def check_args(fail):
    """The regression guard: video mode keeps recoding and the resolution."""
    for url in ("https://x.com/nasa/status/1889912345678",
                "https://www.reddit.com/r/pics/comments/1abc2d/a_title/",
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ"):
        args = build_ytdlp_args(url, _cfg("video"))
        if "--recode-video" not in args:
            fail(f"{url}: video mode lost --recode-video")
        if "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best" not in args:
            fail(f"{url}: video mode lost the 1080 selector")
        if "--yes-playlist" in args:
            fail(f"{url}: must not be treated as a playlist")

    # Carousels and media mode both need the permissive selector instead.
    ig = build_ytdlp_args("https://www.instagram.com/p/Cabc123_x/", _cfg("video"))
    if "--recode-video" in ig or "best" not in ig or "--yes-playlist" not in ig:
        fail("instagram carousel: expected -f best with --yes-playlist")

    media = build_ytdlp_args("https://x.com/nasa/status/1889912345678", _cfg("media"))
    if "--recode-video" in media:
        fail("media mode: must not recode (posts contain images)")
    if "best" not in media:
        fail("media mode: expected the -f best selector")

    audio = build_ytdlp_args("https://x.com/nasa/status/1889912345678", _cfg("audio"))
    if "-x" not in audio:
        fail("audio mode: expected -x")


# The real yt-dlp output for an Instagram image post. Its boilerplate contains
# "please report this issue" and "confirm you are on the latest version", both
# TOOL_FAILURE_MARKERS - so before NO_VIDEO_MARKERS existed this answer forced a
# tool update and burned every retry on a post that simply has no video.
IMAGE_POST_OUTPUT = [
    "[Instagram] Extracting URL: https://www.instagram.com/p/DblkhUwAYDz/",
    "[Instagram] DblkhUwAYDz: Downloading video info",
    "ERROR: [Instagram] DblkhUwAYDz: No video formats found!; please report "
    "this issue on  https://github.com/yt-dlp/yt-dlp/issues?q= , filling out "
    "the appropriate issue template. Confirm you are on the latest version "
    "using  yt-dlp -U",
]

REAL_TOOL_FAILURE = [
    "ERROR: [TikTok] 123: Unable to extract webpage video data; please report "
    "this issue on https://github.com/yt-dlp/yt-dlp/issues",
]


def check_error_classification(fail):
    if not is_no_video(IMAGE_POST_OUTPUT):
        fail("image post: 'No video formats found' must be read as no-video")
    if is_permanent_error(IMAGE_POST_OUTPUT):
        fail("image post: must not be classified permanent")
    if not is_tool_failure(REAL_TOOL_FAILURE):
        fail("genuine 'Unable to extract' must still be a tool failure")
    if is_no_video(REAL_TOOL_FAILURE):
        fail("'Unable to extract' must not be read as no-video")


def main():
    failures = []
    fail = failures.append

    check_urls(fail)
    check_args(fail)
    check_error_classification(fail)

    print(f"Checked {len(POSTS)} post URLs, {len(NON_POSTS)} non-post URLs, "
          f"the yt-dlp arguments for video / media / audio mode, and how "
          f"yt-dlp's errors are classified.")
    print("=" * 60)
    if failures:
        print("FAILURES:")
        for f in failures:
            print("  x " + f)
        return 1
    print("All media-mode URL and argument cases behave correctly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
