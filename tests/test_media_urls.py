#!/usr/bin/env python3
"""Post-URL recognition, naming, and per-mode yt-dlp arguments.

Video/Image mode has to pull every image and video out of a post, but it must
not change what happens to ordinary video links. Three properties matter
enough to guard here, because all of them fail silently:

  * The multi-item predicate stays narrow. It gates ``--yes-playlist`` and the
    ``-f best`` selector, so if an X or Reddit video post ever starts matching
    it, those downloads quietly lose ``--recode-video`` and the user's
    resolution preference.
  * Post patterns match post-shaped paths only. A profile, board or subreddit
    root matching would turn one link into a mass download.
  * An images-only post is recognised as such before anything downloads, and
    an unrecognised one stays undecided rather than being guessed at.

Run:  python3 tests/test_media_urls.py
"""

import json
import os
import pathlib
import sys
import tempfile

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
from mediagrabber.probe import (MAX_ADDRESS_CHARS,             # noqa: E402
                                MAX_CAPTION_WORDS, _has_video,
                                build_name, clean_address,
                                is_carousel_candidate, is_post_url,
                                needs_login, post_folder_name)

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

    # A carousel needs the permissive selector instead.
    ig = build_ytdlp_args("https://www.instagram.com/p/Cabc123_x/", _cfg("video"))
    if "--recode-video" in ig or "best" not in ig or "--yes-playlist" not in ig:
        fail("instagram carousel: expected -f best with --yes-playlist")

    audio = build_ytdlp_args("https://x.com/nasa/status/1889912345678", _cfg("audio"))
    if "-x" not in audio:
        fail("audio mode: expected -x")

    # v3.3 folded "media" into Video/Image. A config written by an older build
    # still says "media"; if that survives load_config the app runs in a mode
    # nothing handles and every format lookup falls through.
    stale = dict(DEFAULTS)
    stale["mode"] = "media"
    with tempfile.TemporaryDirectory() as d:
        path = pathlib.Path(d) / "config.json"
        path.write_text(json.dumps(stale), encoding="utf-8")
        import mediagrabber.config as mgconfig
        original = mgconfig.CONFIG_FILE
        try:
            mgconfig.CONFIG_FILE = path
            migrated = mgconfig.load_config()
        finally:
            mgconfig.CONFIG_FILE = original
    if migrated.get("mode") != "video":
        fail(f"legacy 'media' config should migrate to video, got "
             f"{migrated.get('mode')!r}")


def check_post_contents(fail):
    """The probe decides whether yt-dlp is worth calling at all.

    An images-only post must be knowable *before* downloading, so a photo post
    goes straight to gallery-dl instead of asking yt-dlp for a video that was
    never there. Getting this wrong in the other direction is worse: an
    unknown post must stay unknown, or a plain video is sent to the wrong tool.
    """
    photo = [[3, "https://x/1.jpg", {"extension": "jpg", "video_url": None}]]
    if _has_video(photo) is not False:
        fail("a jpg-only post must be classified as having no video")

    video = [[3, "https://x/1.mp4", {"extension": "mp4"}]]
    if _has_video(video) is not True:
        fail("an mp4 post must be classified as having video")

    tagged = [[3, "https://x/1.jpg", {"extension": "jpg",
                                      "video_url": "https://x/1.mp4"}]]
    if _has_video(tagged) is not True:
        fail("video_url must win over a thumbnail's extension")

    mixed = [[3, "https://x/1.jpg", {"extension": "jpg"}],
             [3, "https://x/2.mp4", {"extension": "mp4"}]]
    if _has_video(mixed) is not True:
        fail("a carousel holding one video must count as having video")

    for unknown in ([], [[3, "https://x/1", {}]],
                    [[3, "https://x/1", {"extension": "xyz"}]]):
        if _has_video(unknown) is not None:
            fail(f"unrecognised items must stay undecided, not guessed: {unknown}")


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



def check_naming(fail):
    """Downloaded files are named "[Address] - [Caption]".

    The rules are small but every one of them is load-bearing on a real
    filesystem: an over-long name fails to write on Windows once the folder
    path is added, a missing half must not leave a dangling separator, and the
    @ must appear exactly once whether or not the source already supplied it.
    """
    ig = "https://www.instagram.com/p/DblkhUwAYDz/"
    yt = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    # An Instagram address is a handle and carries the @; a YouTube channel is
    # a display name and does not.
    if clean_address("pinkbutter", ig) != "@pinkbutter":
        fail("an Instagram address should be prefixed with @")
    if clean_address("@pinkbutter", ig) != "@pinkbutter":
        fail("an address that already has @ must not end up with two")
    if str(clean_address("Rick Astley", yt)).startswith("@"):
        fail("a YouTube channel is a name, not a handle — no @")

    long_addr = clean_address("A Very Long Channel Name That Runs On", yt)
    if len(long_addr) > MAX_ADDRESS_CHARS:
        fail(f"address {long_addr!r} is over {MAX_ADDRESS_CHARS} characters")

    for junk in ("", "   ", None, "///"):
        if clean_address(junk, ig) is not None:
            fail(f"unusable address {junk!r} should be None, not a name")

    caption = "Pink ketemu butter yellow today at the cafe with friends"
    name = build_name(ig, "pinkbutter", caption)
    if not name.startswith("@pinkbutter - "):
        fail(f"expected '@pinkbutter - ...', got {name!r}")
    words = name.split(" - ", 1)[1].split()
    if len(words) > MAX_CAPTION_WORDS:
        fail(f"caption kept {len(words)} words, max is {MAX_CAPTION_WORDS}")

    # Either half missing must not leave a dangling separator.
    for addr, cap in ((None, caption), ("pinkbutter", None), (None, None)):
        got = build_name(ig, addr, cap)
        if got.startswith(" - ") or got.endswith(" - ") or got.strip() == "-":
            fail(f"address={addr!r} caption={cap!r} produced {got!r}")
        if not got.strip():
            fail(f"address={addr!r} caption={cap!r} produced an empty name")

    # No caption still identifies the post, not just the account - otherwise
    # every post by one person collides on one name.
    no_caption = build_name(ig, "pinkbutter", None)
    if "DblkhUwAYDz" not in no_caption:
        fail(f"a captionless post must stay identifiable, got {no_caption!r}")

    # Nothing may contain a character Windows rejects in a filename.
    for bad in '<>:"/|?*':
        probe = build_name(ig, f"we{bad}ird", f"cap{bad}tion here now please")
        if bad in probe:
            fail(f"{bad!r} survived into the filename {probe!r}")


def check_rename_applies_naming(fail):
    """The yt-dlp path has to reach the same names as the gallery-dl path.

    yt-dlp cannot express "first five words" in an output template, so it
    writes a marker name and the policy is applied on rename. If that marker
    and the regex that reads it ever drift apart, files keep their raw titles
    and nothing looks broken until you open the folder.
    """
    import tempfile
    from mediagrabber.download import build_ytdlp_args, rename_temp_files

    template = build_ytdlp_args(
        "https://www.youtube.com/watch?v=x", _cfg("video"))
    template = template[template.index("-o") + 1]
    for marker in ("__MGA_", "__MGI_", "_MGTMP_"):
        if marker not in template:
            fail(f"the output template lost the {marker} marker")
    if not template.startswith("%(title)"):
        fail("the title must lead, or an empty first field makes a dotfile "
             "that the cleanup glob cannot see")

    with tempfile.TemporaryDirectory() as d:
        out = pathlib.Path(d)
        for i in (1, 2, 3):
            (out / f"Slide one caption here friends indeed"
                   f".__MGA_pinkbutter__MGI_{i}__._MGTMP_.jpg").write_text("x")
        (out / "Never Gonna Give You Up Again"
               ".__MGA_Rick Astley__MGI_0__._MGTMP_.mp4").write_text("x")

        names = sorted(f.name for f in rename_temp_files(
            out, "https://www.instagram.com/p/DblkhUwAYDz/", 5))

        if any("_MGTMP_" in n or "__MGA_" in n for n in names):
            fail(f"a temp marker survived into a final name: {names}")

        numbered = [n for n in names if n.startswith("@pinkbutter")]
        if len(numbered) != 3:
            fail(f"expected 3 numbered carousel items, got {numbered}")
        for want in ("- 01.jpg", "- 02.jpg", "- 03.jpg"):
            if not any(n.endswith(want) for n in numbered):
                fail(f"carousel item ending {want} is missing from {numbered}")

        # Every item shares the folder's stem, so the folder and its contents
        # read as one thing.
        stems = {n.rsplit(" - ", 1)[0] for n in numbered}
        if stems != {"@pinkbutter - Slide one caption here friends"}:
            fail(f"carousel items disagree on their shared name: {stems}")


def main():
    failures = []
    fail = failures.append

    check_urls(fail)
    check_args(fail)
    check_naming(fail)
    check_rename_applies_naming(fail)
    check_post_contents(fail)
    check_error_classification(fail)

    print(f"Checked {len(POSTS)} post URLs, {len(NON_POSTS)} non-post URLs, "
          f"the yt-dlp arguments per mode, the legacy-mode migration, "
          f"images-vs-video detection, how yt-dlp's errors are classified, "
          f"and the '[Address] - [Caption]' naming on both download paths.")
    print("=" * 60)
    if failures:
        print("FAILURES:")
        for f in failures:
            print("  x " + f)
        return 1
    print("All URL, argument and post-content cases behave correctly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
