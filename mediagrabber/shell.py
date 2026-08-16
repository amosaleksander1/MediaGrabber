"""Thin subprocess helpers shared by the tool, cookie and download layers."""

import subprocess

from .platform_support import NO_WINDOW


def run_quiet(args, timeout=60):
    """Run a command; return (returncode, stdout+stderr). Never raises."""
    try:
        r = subprocess.run(
            [str(a) for a in args],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
            creationflags=NO_WINDOW,
        )
        return (r.returncode, (r.stdout or "") + (r.stderr or ""))
    except Exception as e:
        return (-1, str(e))


def popen_stream(args):
    """Start a process with merged stdout/stderr decoded as text."""
    return subprocess.Popen(
        [str(a) for a in args],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=NO_WINDOW,
    )


def stop_process(process):
    """Terminate a child process, escalating to kill if it ignores us."""
    try:
        process.terminate()
        try:
            process.wait(timeout=5)
        except Exception:
            process.kill()
    except Exception:
        pass
