"""HTTP helpers: JSON fetch, resumable-ish file download with progress."""

import json
import time
from pathlib import Path
from urllib.request import Request, urlopen

from .ui import C, log

USER_AGENT = "MediaGrabber/3.0"


def fetch_json(url, timeout=30):
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def resolve_redirect(url, timeout=30):
    """Follow redirects and return the final URL (used as a version key for
    services that expose 'latest' as a redirect rather than an API)."""
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=timeout) as resp:
        return resp.geturl()


def download_file(url, dest, desc="file", max_retries=3):
    """Download with a progress line and retries on connection errors."""
    dest_path = Path(dest)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    for attempt in range(1, max_retries + 1):
        try:
            if attempt > 1:
                wait = attempt * 3
                log(f"Retry {attempt}/{max_retries} for {desc} in {wait}s...", "WARN")
                time.sleep(wait)
            else:
                log(f"Downloading {desc}...", "UPDATE")

            req = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(req, timeout=180) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                with open(dest_path, "wb") as f:
                    while True:
                        chunk = resp.read(1024 * 256)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            pct = downloaded * 100 // total
                            print(f"\r  {C.DIM}{downloaded / 1048576:.1f}/"
                                  f"{total / 1048576:.1f} MB ({pct}%){C.RESET}",
                                  end="", flush=True)
                print()

            if total > 0 and downloaded < total:
                log(f"Incomplete download ({downloaded}/{total} bytes), retrying...", "WARN")
                continue

            log(f"{desc} downloaded OK ({downloaded / 1048576:.1f} MB)", "OK")
            return True

        except (ConnectionResetError, ConnectionAbortedError, OSError) as e:
            print()
            log(f"Connection error downloading {desc}: {e}", "WARN")
            if attempt == max_retries:
                log(f"Failed to download {desc} after {max_retries} attempts.", "ERROR")
                _cleanup(dest_path)
                return False
            continue

        except Exception as e:
            print()
            log(f"Failed to download {desc}: {e}", "ERROR")
            _cleanup(dest_path)
            return False

    return False


def _cleanup(path):
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass
