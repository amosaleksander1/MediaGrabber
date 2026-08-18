# MediaGrabber

Portable, menu-driven media downloader for **Windows, macOS and Linux**. Paste links, pick a format, done.

Built on [yt-dlp](https://github.com/yt-dlp/yt-dlp) and [gallery-dl](https://github.com/mikf/gallery-dl), with automatic tool updates, whole-post image and video downloads, and browser-based login.

## Features

- **Batch or single downloads** — queue links in `urls.txt` or paste one at a time
- **Video, audio & media modes** — MP4/MKV/WebM/… video, MP3/FLAC/Opus/… audio, resolution capping, or **Media mode**: pull *every* image and video out of a post
- **Images, not just video** — posts on Instagram, TikTok, X/Twitter, Reddit, Pinterest and Threads are recognised, named from their caption, and downloaded whole. Image-only posts that yt-dlp cannot touch are picked up by gallery-dl automatically, on any site it supports
- **Instagram & TikTok carousels** — detects real multi-item posts and downloads *all* slides (images **and** videos) into a subfolder named from the post caption (e.g. `pink ketemu butter yellow/`), with files named to match (`pink ketemu butter yellow - 01.jpg`). Single reels/posts download normally — no folder.
- **Baked-in login** — borrows your existing browser session (no password stored), with a one-time cookie export so downloads work while the browser is open
- **Self-maintaining** — yt-dlp, ffmpeg, Deno and gallery-dl are auto-downloaded for *your* platform and architecture; update checks run at most every 14 days (or when a tool breaks), so startup is instant
- **Smart retries** — transient errors retry with backoff; permanent errors (private/removed posts) fail fast; extraction errors from an outdated downloader trigger an automatic tool update + retry
- **Graceful stop** — press `Q` during downloads (macOS/Linux: `Q` then Enter) to cancel cleanly

## Download

Grab the archive for your machine from the [latest release](../../releases/latest). Each archive contains **only** the binary that runs on that platform — no cross-platform dead weight.

| Platform | Archive |
| --- | --- |
| Windows 10/11 (64-bit) | `MediaGrabber-windows-x64.zip` |
| macOS — Apple Silicon (M1/M2/M3/M4) | `MediaGrabber-macos-arm64.zip` |
| macOS — Intel | `MediaGrabber-macos-x64.zip` |
| Linux (x86-64) | `MediaGrabber-linux-x64.zip` |

Not sure which Mac you have?  → **About This Mac**. "Chip: Apple M…" means arm64; "Processor: Intel…" means x64.

Or run from source on any platform — see [Running from source](#running-from-source).

---

## macOS

### Install

1. Download `MediaGrabber-macos-arm64.zip` (Apple Silicon) or `MediaGrabber-macos-x64.zip` (Intel) from the [latest release](../../releases/latest).
2. Unzip it, then move the whole `MediaGrabber` folder somewhere permanent — `~/Applications/MediaGrabber` or `~/Documents/MediaGrabber` both work. Keep the folder together: the app creates `tools/`, `logs/`, `config.json` and `urls.txt` next to itself.
3. Double-click **`run.command`**.

`run.command` opens Terminal, which is what the menu interface needs. Double-clicking the bare `MediaGrabber` binary works too, but Finder gives it a less useful window.

### First run on macOS — Gatekeeper

The binary is not code-signed with an Apple Developer certificate, so the first launch is blocked. This is expected for open-source tools distributed outside the App Store. Pick either route:

**Route A — right-click (no Terminal needed)**

1. Right-click (or Control-click) `run.command` → **Open**.
2. macOS shows "…cannot be opened because it is from an unidentified developer" with an **Open** button. Click it.
3. If macOS instead says the file "is damaged", use Route B — that message is Gatekeeper's quarantine flag, not actual corruption.

**Route B — clear the quarantine flag**

Open Terminal, then run (adjust the path to wherever you put the folder):

```bash
cd ~/Applications/MediaGrabber
xattr -dr com.apple.quarantine .
chmod +x MediaGrabber run.command
```

Then double-click `run.command`. You only ever do this once per download.

> MediaGrabber clears the quarantine flag on the tools *it* downloads automatically. The flag on the app itself is the one macOS puts there when *you* download it, and only you can clear it.

### First launch

On first run MediaGrabber will:

1. Detect your Mac's architecture and download the matching tools into `tools/`:
   - **yt-dlp** — `yt-dlp_macos`, a universal2 build (works on both Apple Silicon and Intel)
   - **ffmpeg / ffprobe** — native arm64 or x86_64 builds
   - **Deno** — architecture-matched (yt-dlp needs it for YouTube extraction)
   - **gallery-dl** — native arm64 binary on Apple Silicon. **On Intel Macs no binary is published upstream**, so MediaGrabber installs it via pip *into `tools/gallery-dl-pkg`* — nothing is installed system-wide. This needs a working `python3` (see below).
2. Run a checkup: verify each tool executes, detect your browser, export login cookies, probe your Instagram session.
3. Create `urls.txt` and `config.json` next to the app.

Expect the first run to take a few minutes — ffmpeg alone is ~60–90 MB. Later runs start instantly.

### Requirements

- **macOS 12 Monterey or newer.** The official yt-dlp macOS build requires it. MediaGrabber warns you on older versions but cannot work around it.
- **Python 3** — only needed if you run from source, or if you are on an **Intel Mac** (for the gallery-dl pip fallback). Install with:
  ```bash
  xcode-select --install
  ```
  or from [python.org](https://www.python.org/downloads/macos/). Apple Silicon users running the prebuilt binary need nothing.
- A browser where you are logged into instagram.com, for carousel downloads.

### Login on macOS

Menu `[11]` picks the browser. macOS supports **Safari**, Zen, Firefox, Chrome, Edge, Brave, Vivaldi and Opera.

- **Firefox / Zen** — read directly, no prompts. Easiest option, and what I would pick.
- **Chrome / Edge / Brave / Vivaldi / Opera** — cookies are encrypted with a Keychain key ("Chrome Safe Storage"). macOS will show a **"wants to access your keychain"** prompt the first time; click **Always Allow**. If you deny it or it fails, MediaGrabber automatically retries by launching the browser headless and asking it to hand over its own cookies — no Keychain prompt on that path.
- **Safari** — needs **Full Disk Access** for your terminal:
  **System Settings → Privacy & Security → Full Disk Access →** enable **Terminal** (or iTerm), then fully quit and reopen it. Without this, Safari cookies read as empty.

Cookies are exported once to `tools/cookies.txt` and reused, so downloads keep working whether or not the browser is open.

### macOS troubleshooting

| Symptom | Fix |
| --- | --- |
| `"MediaGrabber" cannot be opened because the developer cannot be verified` | Route A or B above. |
| `"MediaGrabber" is damaged and can't be opened` | Quarantine flag, not damage: `xattr -dr com.apple.quarantine .` in the app folder. |
| `zsh: bad CPU type in executable` | Wrong archive for your Mac (Intel build on Apple Silicon or vice versa). Download the other one. MediaGrabber also self-heals this for its *tools* by re-downloading them. |
| `run.command` opens and closes instantly | Run it from Terminal instead (`./run.command`) to see the error, or check the newest file in `logs/`. |
| Keychain prompt appears repeatedly | Click **Always Allow**, not **Allow**. Or switch to Firefox/Zen via menu `[11]`. |
| Safari cookies come back empty | Full Disk Access for Terminal, then restart Terminal. |
| Intel Mac: `gallery-dl` missing after install | The pip fallback needs `python3`: `xcode-select --install`, then menu `[7]`. |
| Gatekeeper blocks a *tool* in `tools/` | Shouldn't happen — MediaGrabber clears quarantine on what it downloads. If it does: `xattr -dr com.apple.quarantine tools/`. |

---

## Windows

1. Download `MediaGrabber-windows-x64.zip` from the [latest release](../../releases/latest) and unzip it somewhere permanent.
2. Double-click **`MediaGrabber.exe`** (or `RUN.bat` if you run from source).
3. SmartScreen may show "Windows protected your PC" — click **More info → Run anyway**. Same unsigned-binary situation as macOS.

**Login note:** since Chrome v127, Chromium browsers on Windows use App-Bound Encryption that download tools cannot read ([yt-dlp#10927](https://github.com/yt-dlp/yt-dlp/issues/10927)). MediaGrabber works around it automatically by launching your browser headless with the DevTools Protocol and asking it to decrypt its *own* cookies. No browser settings are changed. Firefox/Zen need no workaround.

---

## Linux

Download `MediaGrabber-linux-x64.zip`, unzip, then:

```bash
chmod +x MediaGrabber run.sh
./run.sh
```

The prebuilt `gallery-dl` binary needs **glibc ≥ 2.38** (Ubuntu 24.04+, Fedora 39+). On older distros MediaGrabber detects the failure and falls back to a pip install inside `tools/` automatically — no action needed, as long as `python3` and `pip` exist.

---

## Running from source

Works identically on all three platforms. No third-party Python packages are required — the app is 100% standard library.

```bash
git clone https://github.com/amosaleksander1/MediaGrabber.git
cd MediaGrabber

python3 mediagrabber.py     # macOS / Linux
python mediagrabber.py      # Windows
```

Requires Python 3.8+.

### Building a binary yourself

```bash
./BUILD.sh      # macOS / Linux — produces ./MediaGrabber for the current arch
BUILD.bat       # Windows — produces MediaGrabber.exe
```

Both use PyInstaller. A macOS build is native to whichever architecture you build on: build on Apple Silicon for arm64, on an Intel Mac for x86_64. There is no cross-compilation — that is why CI builds on both `macos-14` and `macos-13`.

---

## The menu

```
[1]  Download from urls.txt        [7]  Force update tools now
[2]  Download single URL           [8]  Open output folder
[3]  Change format (Video/Audio/   [9]  Open urls.txt for editing
     Media)
[4]  Change resolution             [10] Checkup (tools + login)
[5]  Change output folder          [11] Set login cookie browser
[6]  Toggle auto-update            [12] Delete saved login cookies
[0]  Exit
```

## How login works (no password stored)

Instagram rejects anonymous downloads, and scripted username/password logins get blocked (and break with 2FA). MediaGrabber instead **borrows the session cookies from a browser where you are already logged in**:

1. Menu `[11]` — pick your browser.
2. The app exports your cookies **once** to `tools/cookies.txt`.
3. All later downloads use the cached file, browser open or not. If the session expires, the checkup detects it, re-exports, and re-probes automatically.

> ⚠️ `tools/cookies.txt` **is your live Instagram session**. Anyone with that file can act as your account. It is `.gitignore`d — never commit or share it. Use menu `[12]` to delete it at any time (it re-exports automatically when next needed).

## Pulling images and media out of a post

### Media mode

Menu `[3]` → `[3]` switches to **Media** mode, which treats a link as a *post* rather than a video: gallery-dl pulls every item it holds — images, videos, or a mix — instead of yt-dlp looking for a single video stream. Use it for image posts, photo carousels and mixed albums. Resolution settings do not apply (an image has no bitrate); switch back to Video mode for resolution capping.

In Video and Audio mode nothing changes, with one addition: when yt-dlp exhausts its retries on a link, gallery-dl is asked whether it can pull media from that post anyway. That is what rescues an image-only post you paste without switching modes. On a site gallery-dl has no extractor for, the attempt simply misses and the original error stands.

### Recognised post links

These are probed for their item count and named from their caption:

| Platform | Link shape |
| --- | --- |
| Instagram | `instagram.com/p/…`, `/reel/…`, `/tv/…` |
| TikTok | `tiktok.com/@user/photo/…`, `/video/…`, `vt.tiktok.com/…` |
| X / Twitter | `x.com/user/status/…`, `twitter.com/user/status/…` |
| Reddit | `reddit.com/r/sub/comments/…`, `redd.it/…` |
| Pinterest | `pinterest.*/pin/…`, `pin.it/…` |
| Threads | `threads.net/@user/post/…` |

Only *post-shaped* links match — a profile, board or subreddit root is deliberately not treated as a post, so one link can never turn into a mass download. A post with no readable caption falls back to a name derived from the link (`reddit_pics_1abc2d`, `twitter_nasa_1889…`).

### What happens to a post link

Any recognised post is probed first (metadata only):

1. **Single item** → downloaded normally into the output folder, standard naming. If yt-dlp cannot handle it (e.g. a single image post), gallery-dl takes over.
2. **2+ items (real carousel)** → a subfolder is created, named from the first caption words (default 4 — `folder_name_words` in `config.json`; emojis, hashtags, mentions and links stripped; no caption → `instagram_<shortcode>`). **gallery-dl** downloads every slide — images and videos — into it, named `<folder name> - 01.jpg`, `- 02.mp4`, … instead of numeric media IDs.
3. If gallery-dl fails, the app falls back to yt-dlp targeting the same folder.

Regular links (YouTube etc.) are unaffected and go straight into the output folder, keeping `--recode-video` and your resolution setting.

> Probing costs one metadata request per post link, so a large `urls.txt` of post URLs starts a little slower than a list of plain video links.

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
| `cookies_browser` | `auto` | `zen`, `firefox`, `chrome`, `edge`, `brave`, `vivaldi`, `opera`, `safari` (macOS), `auto`, `none` |
| `folder_name_words` | `4` | caption words used for carousel folder names |

## General troubleshooting

**`HTTP redirect to login page`**
Your cached session is missing or expired. Log into instagram.com in your chosen browser, then run menu `[10]`.

**Carousel folder named `instagram_XXXX` instead of the caption**
The post has no caption, or the metadata fetch failed (check the log for `No usable caption found`). Usually an auth issue — run menu `[10]`.

**Rate limiting / temporary blocks from Instagram**
Space out large batches. Instagram throttles aggressive scraping per account.

**Something else**
Every session writes a timestamped log to `logs/`. The newest file there has the full command output.

---

## Project layout

```
mediagrabber.py            launcher shim (the historic entry point)
mediagrabber/
  platform_support.py      every Windows/macOS/Linux difference lives here
  config.py                paths, defaults, format lists
  tools.py                 per-platform tool sources + auto-update
  cookies.py               browser login, per-OS cookie decryption
  probe.py                 post detection, item count and naming
  download.py              the download engine
  checkup.py               health check
  app.py                   menu loop
  ui.py / net.py / shell.py  output, HTTP, subprocess helpers
scripts/check_upstream.py  weekly upstream-asset verification (CI)
tests/                     platform matrix test
```

Adding a platform is deliberately a `platform_support.py` + `tools.py` change; nothing else tests `sys.platform` directly.

## Releases and changelog

Releases are cut by pushing a version tag; CI builds all four platform archives and publishes them with a changelog generated from the commits since the previous tag. **The changelog lives on the [GitHub Releases](../../releases) page** — this repo intentionally has no `CHANGELOG.md`.

```bash
git tag -a v3.0.1 -m "Short summary of the release"
git push --follow-tags
```

A scheduled workflow additionally checks every Monday that the upstream binaries MediaGrabber depends on still exist for all five platform targets, opening a PR when versions move and an issue when an asset disappears.

## Legal

Download only content you have the right to save. Respect the Terms of Service of the platforms you use and the copyright of content owners. This tool is for personal archiving of content you are authorized to access.

## License

MIT — see [LICENSE](LICENSE).
