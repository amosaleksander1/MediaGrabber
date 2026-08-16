#!/bin/bash
# One-time migration: turn the old CHANGELOG.md into real git tags and GitHub
# Releases, so removing the file loses nothing.
#
# This repo never had tags — the entire v1.x/v2.x history lived only in
# CHANGELOG.md. From v3.0.0 onward the release workflow generates notes from
# commits automatically; this script backfills everything before that.
#
# Requires the GitHub CLI, authenticated:  gh auth status
# Safe to re-run: existing tags and releases are skipped.

set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

if ! command -v gh >/dev/null 2>&1; then
    echo "ERROR: the GitHub CLI (gh) is required. https://cli.github.com/"
    exit 1
fi
gh auth status >/dev/null 2>&1 || { echo "ERROR: run 'gh auth login' first."; exit 1; }

tag_at() {
    local tag="$1" sha="$2"
    if git rev-parse -q --verify "refs/tags/$tag" >/dev/null; then
        echo "  tag $tag already exists — skipping"
        return 0
    fi
    if ! git cat-file -e "${sha}^{commit}" 2>/dev/null; then
        echo "  commit $sha not found — skipping $tag"
        return 1
    fi
    git tag -a "$tag" "$sha" -m "MediaGrabber $tag"
    echo "  created tag $tag at $sha"
}

release() {
    local tag="$1" title="$2" notes="$3"
    if gh release view "$tag" >/dev/null 2>&1; then
        echo "  release $tag already exists — skipping"
        return 0
    fi
    gh release create "$tag" --title "$title" --notes "$notes" >/dev/null
    echo "  published release $tag"
}

echo "Creating tags..."
tag_at v2.0.0 b84fa16
tag_at v2.1.0 57e8623
tag_at v2.2.0 cf99186
tag_at v2.2.1 c4c30a5
tag_at v2.3.0 a900c3d
tag_at v2.4.0 c6180c3
tag_at v2.4.1 ee569a2

echo
echo "Pushing tags..."
git push origin --tags

echo
echo "Publishing releases..."

release v2.0.0 "MediaGrabber v2.0.0 — Linux support" '### Added
- **Linux support** — platform-aware tool downloads (yt-dlp, ffmpeg `.tar.xz`, Deno, `gallery-dl_linux`), executable permissions, Linux browser cookie paths, `xdg-open`, and a `run.sh` launcher.
- MIT license, README, `.gitignore` (protects `cookies.txt` from being committed), initial GitHub repository.

### Changed
- Default output folder is now `~/Downloads/MediaGrabber` (override via `config.json` / menu `[5]`).

_Earlier history (v1.1 – v1.6): initial menu-driven yt-dlp/ffmpeg downloader, carousel support via gallery-dl, browser-session login, cookie cache, Zen Browser support, and caption-based carousel folders._'

release v2.1.0 "MediaGrabber v2.1.0 — throttled updates, single-post detection" '### Added
- **Throttled update checks** — tools are checked at most every 14 days (`_last_check` in `tools/versions.json`), so startup is instant. Forced via menu `[7]`, or automatically when a tool is missing or fails mid-download.
- **Single-post detection** — IG/TikTok links are probed (metadata only) before downloading. Single reels/posts download normally; only real carousels (2+ items) get a subfolder.
- **Named carousel files** — slides saved as `<caption> - 01.jpg`, `- 02.mp4`, … instead of numeric media IDs.
- **gallery-dl fallback for single posts** — single image posts yt-dlp cannot handle are downloaded via gallery-dl, named from the caption.
- **Menu `[12]` Delete saved login cookies** — removes `tools/cookies.txt` on demand; re-exported automatically when next needed.
- **Self-healing tools** — a missing/corrupt downloader binary triggers an immediate reinstall and the download retries.

### Changed
- Cold-start checkup is now *quick*: existence checks only, and the live Instagram auth probe runs only when no cookie cache exists. Full checkup remains on menu `[10]`.'

release v2.2.0 "MediaGrabber v2.2.0 — graceful stop, outdated-tool recovery" '### Added
- **Graceful stop** — press `Q` during any download (Windows: instant; Linux: `Q` then Enter) to cancel cleanly and return to the menu. Partial temp files are removed; in batch mode remaining URLs stay in `urls.txt`. No more Ctrl+C closing the whole app.
- **Outdated-tool auto-recovery** — extraction errors indicating an outdated downloader (`Unable to extract`, `JS challenge`, HTTP 403) now trigger an automatic forced tool update and a free retry, bypassing the 14-day throttle.'

release v2.2.1 "MediaGrabber v2.2.1 — TikTok UA fix" '### Fixed
- **TikTok "Unexpected response from webpage request"** — TikTok started rejecting yt-dlp'"'"'s default User-Agent worldwide on 2026-08-10 ([yt-dlp#17403](https://github.com/yt-dlp/yt-dlp/issues/17403)). Both engines now send a real Chrome UA (+ TikTok Referer for yt-dlp) on tiktok.com URLs.
- gallery-dl last-resort fallback now also covers TikTok `/video/` links, not just carousel-style URLs.

### Added
- `[Press Q to cancel]` reminder shown inline on the download progress bar.'

release v2.3.0 "MediaGrabber v2.3.0 — TikTok embed-page engine" '### Added
- **TikTok embed-page engine** — when both yt-dlp and gallery-dl fail on a TikTok video (ongoing upstream breakage, [yt-dlp#17403](https://github.com/yt-dlp/yt-dlp/issues/17403)), the app pulls the signed `tiktokcdn.com` media URL from TikTok'"'"'s official embed page and downloads it directly with browser headers. Supports Q-cancel and progress display like the other engines.'

release v2.4.0 "MediaGrabber v2.4.0 — Chrome cookie borrowing (App-Bound Encryption)" '### Added
- **Chrome cookie borrowing now works** (Chrome/Edge/Brave/Vivaldi/Opera on Windows). Since Chrome v127, cookies use App-Bound Encryption that yt-dlp and gallery-dl cannot decrypt ([yt-dlp#10927](https://github.com/yt-dlp/yt-dlp/issues/10927)). MediaGrabber works around it by launching the real browser headless with the DevTools Protocol enabled, asking the browser to decrypt its **own** cookies, and exporting them to `tools/cookies.txt`. No Chrome security settings are changed.
- Minimal stdlib WebSocket client (no new dependencies) for the DevTools call.

### Changed
- Auto-detect no longer implies "prefer Firefox" for cookies — Chromium browsers are now first-class for login borrowing.'

release v2.4.1 "MediaGrabber v2.4.1 — Chrome cookie export fixes" '### Fixed
- **Chrome cookie export returning zero cookies / permission-denied.** The v2.4.0 approach copied the profile to a temp dir, which broke App-Bound Encryption key unwrapping (0 cookies) and tripped over the locked cookie DB while Chrome ran. Now points headless Chrome at the **real** profile directory (required for ABE keys to unwrap), detects when the browser is still running and prompts to close it once, and adds a `Storage.getCookies` fallback for Chrome builds where `Network.getAllCookies` returns nothing.'

echo
echo "Done. Historical changelog is now on the Releases page:"
gh repo view --json url -q .url | sed 's|$|/releases|'
