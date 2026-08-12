# MediaGrabber

Portable, menu-driven media downloader for Windows and Linux. Paste links, pick a format, done.
Built on [yt-dlp](https://github.com/yt-dlp/yt-dlp) and [gallery-dl](https://github.com/mikf/gallery-dl), with automatic tool updates, Instagram/TikTok carousel support, and browser-based login.

## Features

- **Batch or single downloads** — queue links in `urls.txt` or paste one at a time
- **Video & audio modes** — MP4/MKV/WebM/… video, MP3/FLAC/Opus/… audio, resolution capping
- **Instagram & TikTok carousels** — detects real multi-item posts, downloads *all* slides (images **and** videos) into a subfolder named from the post caption (e.g. `pink ketemu butter yellow/`), with files named to match (`pink ketemu butter yellow - 01.jpg`). Single reels/posts download normally — no folder.
- **Baked-in login** — borrows your existing browser session (no password stored), with a one-time cookie export so downloads work while the browser is open
- **Self-maintaining, never in your way** — yt-dlp, ffmpeg, Deno and gallery-dl are auto-downloaded; update checks run at most every 14 days (or when a tool breaks), so startup is instant and downloads begin immediately
- **Quick cold-start checkup** — instant verification that tools and login are in place; full live probe on demand via menu `[10]`
- **Smart retries** — transient errors retry with backoff; permanent errors (private/removed posts) fail fast; extraction errors from an outdated downloader trigger an automatic tool update + retry
- **Graceful stop** — press `Q` during downloads (Linux: `Q` then Enter) to cancel cleanly without killing the app

## Requirements

- Python 3.8+ (Windows users can also run the prebuilt `.exe`, see *Building*)
- Internet access to GitHub releases (for the auto-downloaded tools)
- A browser where you're logged into instagram.com (for carousel downloads)
- Linux: the prebuilt `gallery-dl` binary needs glibc ≥ 2.38 (Ubuntu 24.04+, Fedora 39+).
  On older distros install it via `pip install gallery-dl` and symlink it into `tools/`.

No Python packages are required — the app is 100 % standard library.

## Quick start

```bash
git clone https://github.com/<you>/MediaGrabber.git
cd MediaGrabber
python mediagrabber.py        # python3 on Linux
```

First launch will:

1. Download yt-dlp, ffmpeg, Deno and gallery-dl into `tools/`
2. Run a **checkup**: verify each tool executes, detect your browser, export login cookies, and probe your Instagram session
3. Create `urls.txt` and `config.json` next to the script

Then use the menu:

```
[1]  Download from urls.txt        [7]  Force update tools now
[2]  Download single URL           [8]  Open output folder
[3]  Change format (Video/Audio)   [9]  Open urls.txt for editing
[4]  Change resolution             [10] Checkup (tools + login)
[5]  Change output folder          [11] Set login cookie browser
[6]  Toggle auto-update            [12] Delete saved login cookies
[0]  Exit
```

## How login works (no password stored)

Instagram rejects anonymous downloads, and scripted username/password logins get blocked
(and break with 2FA). MediaGrabber instead **borrows the session cookies from a browser
where you're already logged in**:

1. Menu `[11]` — pick your browser. Supported: **Zen**, Firefox, Chrome, Edge, Brave, Vivaldi, Opera. `auto` prefers Firefox-family browsers because Chromium browsers lock their cookie database while running.
2. The app exports your cookies **once** to `tools/cookies.txt`. If a Chromium browser is holding the lock, the app asks you to close it, then retries — that's the only time closing your browser is needed.
3. All later downloads use the cached file, browser open or not. If the session expires, the checkup detects it, re-exports, and re-probes automatically.

> ⚠️ `tools/cookies.txt` **is your live Instagram session**. Anyone with that file can act
> as your account. It is `.gitignore`d — never commit or share it. Use menu `[12]`
> to delete it any time (it re-exports automatically when next needed).

## Carousel downloads

Any Instagram post/reel (`instagram.com/p/…`, `/reel/…`) or TikTok photo post
(`tiktok.com/@user/photo/…`, `vt.tiktok.com/…`) is probed first (metadata only):

1. **Single item** → downloaded normally into the output folder, standard naming.
   If yt-dlp can't handle it (e.g. a single image post), gallery-dl takes over.
2. **2+ items (real carousel)** → a subfolder is created, named from the first
   caption words (default 4 — `folder_name_words` in `config.json`; emojis,
   hashtags, mentions and links stripped; no caption → `instagram_<shortcode>`).
   **gallery-dl** downloads every slide — images and videos — into it, with
   files named `<folder name> - 01.jpg`, `- 02.mp4`, … instead of numeric media IDs.
3. If gallery-dl fails, the app falls back to yt-dlp targeting the same folder.

Regular links (YouTube etc.) are unaffected and go straight into the output folder.

## Configuration (`config.json`)

| Key | Default | Meaning |
| --- | --- | --- |
| `mode` | `video` | `video` or `audio` |
| `video_format` | `mp4` | container for video mode |
| `audio_format` | `mp3` | format for audio mode (`-x`) |
| `resolution` | `best` | `best`, `worst`, or a height cap like `1080` |
| `output_dir` | `~/Downloads/MediaGrabber` | where files land |
| `auto_update` | `true` | update tools on startup |
| `max_retries` | `3` | retry count for transient errors |
| `cookies_browser` | `auto` | `zen`, `firefox`, `chrome`, `edge`, `brave`, `vivaldi`, `opera`, `auto`, `none` |
| `folder_name_words` | `4` | caption words used for carousel folder names |

## Troubleshooting

**`Permission denied … Cookies` / `Could not copy Chrome cookie database`**
A Chromium browser is running and locking its cookie DB. Close it completely (tray icon
too) and run menu `[10]`. Once `tools/cookies.txt` exists this stops mattering.
Firefox/Zen don't have this problem — prefer them via menu `[11]`.

**`HTTP redirect to login page`**
Your cached session is missing or expired. Log into instagram.com in your chosen
browser, then run menu `[10]`.

**Carousel folder named `instagram_XXXX` instead of the caption**
The post has no caption, or the metadata fetch failed (check the log for
`No usable caption found`). Usually an auth issue — run menu `[10]`.

**Linux: `GLIBC_2.38 not found` when gallery-dl runs**
Your distro is too old for the prebuilt binary:
`pip install gallery-dl && ln -sf $(which gallery-dl) tools/gallery-dl`

**Rate limiting / temporary blocks from Instagram**
Space out large batches. Instagram throttles aggressive scraping per-account.

## Building a Windows .exe

```bat
BUILD.bat
```
(uses PyInstaller; output lands in `dist/`). Linux users just run the script directly.

## Legal

Download only content you have the right to save. Respect the Terms of Service of the
platforms you use and the copyright of content owners. This tool is for personal
archiving of content you're authorized to access.

## License

MIT — see [LICENSE](LICENSE).
