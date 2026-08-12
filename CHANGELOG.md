# Changelog

All notable changes to MediaGrabber.

## [2.2.1] — 2026-08-12

### Fixed
- **TikTok "Unexpected response from webpage request"** — TikTok started
  rejecting yt-dlp's default User-Agent worldwide on 2026-08-10 (upstream
  issue yt-dlp#17403). Both engines now send a real Chrome UA (+ TikTok
  Referer for yt-dlp) on tiktok.com URLs.
- gallery-dl last-resort fallback now also covers TikTok `/video/` links,
  not just carousel-style URLs.

### Added
- `[Press Q to cancel]` reminder shown inline on the download progress bar.

## [2.2.0] — 2026-08-12

### Added
- **Graceful stop** — press `Q` during any download (Windows: instant;
  Linux: `Q` then Enter) to cancel cleanly and return to the menu. Partial
  temp files are removed; in batch mode remaining URLs stay in `urls.txt`.
  No more Ctrl+C closing the whole app.
- **Outdated-tool auto-recovery** — extraction errors that indicate an
  outdated downloader (e.g. TikTok's JS challenge breaking older yt-dlp:
  `Unable to extract`, `JS challenge`, HTTP 403) now trigger an automatic
  forced tool update and a free retry, bypassing the 14-day throttle.

## [2.1.0] — 2026-07-31

### Added
- **Throttled update checks** — tools are checked at most every 14 days
  (`_last_check` in `tools/versions.json`), so startup is instant and downloads
  begin immediately. Forced checks via menu `[7]`, or automatically when a tool
  is missing or fails mid-download.
- **Single-post detection** — IG/TikTok links are probed (metadata only) before
  downloading. Single reels/posts download normally into the output folder with
  standard naming; only real carousels (2+ items) get a subfolder.
- **Named carousel files** — slides are saved as `<caption> - 01.jpg`,
  `- 02.mp4`, … instead of numeric media IDs.
- **gallery-dl fallback for single posts** — single image posts that yt-dlp
  can't handle are downloaded via gallery-dl, named from the caption.
- **Menu `[12]` Delete saved login cookies** — removes `tools/cookies.txt` on
  demand to keep the security footprint low; re-exported automatically when
  next needed.
- **Self-healing tools** — a missing/corrupt downloader binary triggers an
  immediate reinstall and the download retries.

### Changed
- Cold-start checkup is now *quick*: existence checks only, and the live
  Instagram auth probe runs only when no cookie cache exists. Full checkup
  remains on menu `[10]`.

## [2.0.0] — 2026-07-31

### Added
- **Linux support** — platform-aware tool downloads (yt-dlp, ffmpeg `.tar.xz`,
  Deno, `gallery-dl_linux`), executable permissions, Linux browser cookie
  paths, `xdg-open`, and a `run.sh` launcher.
- MIT license, README, `.gitignore` (protects `cookies.txt` from being
  committed), initial GitHub repository.

### Changed
- Default output folder is now `~/Downloads/MediaGrabber` (override via
  `config.json` / menu `[5]`).

## [1.6.x]

### Added
- **Caption-based carousel folders** — folder named from the first N words of
  the post caption (default 4, `folder_name_words`), stripping emojis,
  hashtags, mentions and URLs; falls back to `instagram_<shortcode>`.

### Fixed
- Caption metadata parser now handles gallery-dl's pretty-printed JSON and
  leading `[instagram]` warning lines.

## [1.5.x]

### Added
- **Cookie cache** — login cookies are exported once to `tools/cookies.txt`
  so downloads work while the browser is open (Chromium browsers lock their
  cookie DB). Stale sessions are detected, re-exported, and re-probed
  automatically.
- **Zen Browser support** — Firefox-based Zen profiles are auto-located and
  passed as `firefox:<profile path>`.

## [1.4.0]

### Added
- **Browser-session login** — `--cookies-from-browser` wired into both engines
  for Instagram/TikTok; browser auto-detection (Firefox-family preferred);
  menu `[11]` to choose the browser.
- **Startup checkup** — verifies each tool runs and probes the Instagram login
  session; menu `[10]`.

## [1.2.0 – 1.3.0]

### Added
- **Carousel support** — Instagram posts/reels and TikTok photo posts download
  all slides (images + videos) into their own subfolder via **gallery-dl**
  (auto-installed from `gdl-org/builds`), with yt-dlp fallback.

### Fixed
- gallery-dl updater pointed at the correct binaries repo (`gdl-org/builds`).
- Temp-file naming collisions for multi-file downloads (playlist index marker).

## [1.1.0]

- Initial MediaGrabber: menu-driven yt-dlp/ffmpeg downloader with auto-update,
  batch `urls.txt` processing, format/resolution pickers, smart retry with
  permanent-error detection, and session logging.
