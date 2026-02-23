"""
SRT caption generator for the Phase 0 preview renderer.

Builds .srt subtitle files from AssetManifest VO lines.
Shot-level absolute timeline positions are computed from cumulative duration_ms.

Sync rules:
  - If a VOLine has explicit timeline_in_ms / timeline_out_ms > 0, those are
    used as offsets relative to the shot's start position in the final timeline.
  - If both are 0 (no timing data yet), the caption spans the full shot duration.
  - Minimum caption display time: 1 000 ms.
  - Minimum gap between adjacent captions: 40 ms.

Output: standard SRT (SubRip Text) format, UTF-8 encoded.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schemas.asset_manifest import AssetManifest

logger = logging.getLogger(__name__)

_MIN_CAPTION_DURATION_MS: int = 1_000   # §15 caption sync check floor
_MIN_CAPTION_GAP_MS: int = 40           # standard SRT inter-caption gap


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_srt(manifest: "AssetManifest") -> str:
    """
    Build SRT subtitle content from all VO lines in the manifest.

    Args:
        manifest: Parsed AssetManifest containing shots with vo_lines.

    Returns:
        Complete SRT string (empty string if there are no captionable VO lines).
    """
    raw: list[tuple[int, int, str]] = []   # (abs_start_ms, abs_end_ms, display_text)

    shot_start_ms = 0
    for shot in manifest.shots:
        for vo in shot.vo_lines:
            if not vo.text.strip():
                continue

            if vo.timeline_in_ms == 0 and vo.timeline_out_ms == 0:
                # No timing data — span the full shot.
                abs_start = shot_start_ms
                abs_end = shot_start_ms + shot.duration_ms
            else:
                abs_start = shot_start_ms + vo.timeline_in_ms
                abs_end = shot_start_ms + vo.timeline_out_ms

            # Enforce minimum display duration.
            if abs_end - abs_start < _MIN_CAPTION_DURATION_MS:
                abs_end = abs_start + _MIN_CAPTION_DURATION_MS

            # Format: "speaker_id: text" when speaker_id is present.
            # speaker_id is preserved as-is (no forced upper-case);
            # callers that want ALL-CAPS labels should upper-case the
            # speaker_id value in the AssetManifest before rendering.
            label = (
                f"{vo.speaker_id}: {vo.text}"
                if vo.speaker_id
                else vo.text
            )
            raw.append((abs_start, abs_end, label))

        shot_start_ms += shot.duration_ms

    if not raw:
        return ""

    # Sort by start time then enforce minimum inter-caption gap.
    raw.sort(key=lambda e: e[0])
    adjusted: list[tuple[int, int, str]] = []
    for start, end, text in raw:
        if adjusted:
            prev_end = adjusted[-1][1]
            if start < prev_end + _MIN_CAPTION_GAP_MS:
                start = prev_end + _MIN_CAPTION_GAP_MS
                end = max(end, start + _MIN_CAPTION_DURATION_MS)
        adjusted.append((start, end, text))

    # Build SRT blocks (1-indexed).
    blocks: list[str] = []
    for idx, (start, end, text) in enumerate(adjusted, start=1):
        blocks.append(
            f"{idx}\n"
            f"{_ms_to_srt(start)} --> {_ms_to_srt(end)}\n"
            f"{text}"
        )

    return "\n\n".join(blocks) + "\n"


def write_srt(manifest: "AssetManifest", output_path: Path) -> Path:
    """
    Write the .srt file for *manifest* to *output_path*.

    Always writes the file (even if empty) so that RenderOutput.captions_uri
    is always populated with a valid path.

    Args:
        manifest:    Parsed AssetManifest.
        output_path: Destination .srt file path.

    Returns:
        *output_path* (for chaining).
    """
    content = build_srt(manifest)
    output_path = Path(output_path)
    output_path.write_text(content, encoding="utf-8")
    logger.info(
        "Wrote captions: %s (%d bytes, %d blocks)",
        output_path,
        len(content),
        content.count("\n\n") + (1 if content else 0),
    )
    return output_path


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ms_to_srt(ms: int) -> str:
    """Convert milliseconds to SRT timestamp format: HH:MM:SS,mmm."""
    ms = max(0, ms)
    hours, remainder = divmod(ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"
