"""
Minimal FFmpeg runner for the Phase 0 pipeline renderer.

Standalone module — zero imports from video/worker or any other internal package.
Suitable for sequential local execution (Phase 0 has no distributed queue).

Required ffmpeg version: >= 6.1 (tested on 6.1.1-3ubuntu5 / libx264).
See video/tools/README.md for version pinning guidance.
"""
from __future__ import annotations

import logging
import os
import re
import signal
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)

# Minimum supported ffmpeg version (MAJOR.MINOR).
# The golden render test stores framemd5 hashes produced by this version;
# a different encoder version may produce different bitstreams.
FFMPEG_MIN_VERSION = "6.1"


class FFmpegError(Exception):
    """FFmpeg subprocess exited with a non-zero return code."""


class FFmpegNotFound(Exception):
    """ffmpeg binary is not available on PATH."""


# ---------------------------------------------------------------------------
# Version helpers
# ---------------------------------------------------------------------------

def get_ffmpeg_version() -> str:
    """
    Return the installed ffmpeg version string (e.g. "6.1.1").

    Raises:
        FFmpegNotFound: if ffmpeg is not on PATH or fails to respond.
    """
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except FileNotFoundError:
        raise FFmpegNotFound(
            "ffmpeg not found on PATH. "
            f"Install ffmpeg >= {FFMPEG_MIN_VERSION} (e.g. `apt install ffmpeg`)."
        )
    except subprocess.CalledProcessError as exc:
        raise FFmpegNotFound(f"ffmpeg -version failed: {exc}")

    # First line format: "ffmpeg version X.Y.Z[-suffix] ..."
    first_line = result.stdout.splitlines()[0]
    parts = first_line.split()
    if len(parts) >= 3 and parts[0] == "ffmpeg" and parts[1] == "version":
        return parts[2]
    return first_line


def validate_ffmpeg() -> str:
    """
    Validate that ffmpeg is present and warn if below minimum version.

    Returns:
        The version string.

    Raises:
        FFmpegNotFound: if ffmpeg is absent.
    """
    version = get_ffmpeg_version()
    # Extract leading MAJOR.MINOR for comparison
    m = re.match(r"(\d+)\.(\d+)", version)
    if m:
        major, minor = int(m.group(1)), int(m.group(2))
        req_major, req_minor = (int(x) for x in FFMPEG_MIN_VERSION.split(".", 1))
        if (major, minor) < (req_major, req_minor):
            logger.warning(
                "ffmpeg %s is below minimum required %s; "
                "golden render hashes may not match.",
                version,
                FFMPEG_MIN_VERSION,
            )
    return version


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_ffmpeg(cmd: list[str], timeout: int = 600) -> None:
    """
    Run an FFmpeg command synchronously in its own process group.

    Args:
        cmd: Complete FFmpeg command as a list of strings.
             Must start with "ffmpeg".
        timeout: Maximum wall-clock seconds to allow (default 600 = 10 min).

    Raises:
        FFmpegNotFound: if the ffmpeg binary is missing.
        FFmpegError:    if FFmpeg exits with a non-zero return code.
        TimeoutError:   if FFmpeg exceeds *timeout* seconds.
    """
    logger.debug("ffmpeg cmd: %s", " ".join(cmd[:10]) + (" ..." if len(cmd) > 10 else ""))

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            preexec_fn=os.setsid,  # own process group → clean kill on timeout
        )
    except FileNotFoundError:
        raise FFmpegNotFound(
            "ffmpeg not found on PATH. "
            f"Install ffmpeg >= {FFMPEG_MIN_VERSION}."
        )

    try:
        _, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(process)
        raise TimeoutError(
            f"FFmpeg exceeded timeout of {timeout}s — killed. "
            f"Command: {' '.join(cmd[:6])} ..."
        )

    if process.returncode != 0:
        # Surface the tail of stderr for diagnosis
        tail = stderr[-3000:] if len(stderr) > 3000 else stderr
        raise FFmpegError(
            f"FFmpeg exited {process.returncode}.\n"
            f"Command: {' '.join(cmd[:8])} ...\n"
            f"stderr (last 3000 chars):\n{tail}"
        )

    logger.debug("FFmpeg finished OK (rc=0)")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _kill_group(process: subprocess.Popen) -> None:
    """Kill the process and its entire process group (SIGKILL)."""
    try:
        pgid = os.getpgid(process.pid)
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass  # already dead
    except Exception as exc:
        logger.warning("Could not kill ffmpeg process group: %s", exc)
        try:
            process.kill()
        except Exception:
            pass
