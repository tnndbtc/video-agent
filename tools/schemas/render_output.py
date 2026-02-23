"""
RenderOutput — canonical schema §5.9 of master plan.

Produced by the renderer:
  - final video path/URI
  - captions path/URI
  - audio stems path/URI (optional; null in Phase 0)
  - hashes, provenance links, lineage references

All URI fields use file:// scheme for local Phase 0 artifacts.
hashes contains SHA-256 content hashes of the output files,
enabling artifact registry lookups and re-render deduplication (§14).
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class OutputHashes(BaseModel):
    """Content hashes for output artifacts (§5.9: hashes)."""
    video_sha256: str
    captions_sha256: Optional[str] = None
    audio_stems_sha256: Optional[str] = None   # always null in Phase 0


class Provenance(BaseModel):
    """
    Render provenance metadata (§5.9: provenance links).
    rendered_at: ISO 8601 wall-clock timestamp (not used for determinism checks;
    the timing_lock_hash + lineage hashes are the reproducibility anchors).
    """
    render_profile: str
    timing_lock_hash: str
    rendered_at: str            # ISO 8601
    ffmpeg_version: str         # e.g. "6.1.1" — must match pinned version for golden tests
    placeholder_count: int = 0  # number of shots that used generated placeholders
    renderer: str = "video"     # identifies the renderer implementation


class Lineage(BaseModel):
    """
    Input artifact hashes for full reproducibility (§5.9: lineage references, §14).
    Enables the artifact registry to reconstruct any render from stored inputs.
    """
    asset_manifest_hash: str    # SHA-256 of the input AssetManifest JSON
    render_plan_hash: str       # SHA-256 of the input RenderPlan JSON


class OutputArtifact(BaseModel):
    """One produced output file with its content hash."""
    type: str      # "video" | "captions"
    path: str      # absolute filesystem path
    sha256: str    # hex SHA-256 of file contents


class EffectiveSettings(BaseModel):
    """Render settings snapshot — enables determinism proofs."""
    resolution: str   # e.g. "1280x720"
    fps: str          # e.g. "24"
    audio_rate: str   # codec name or "none"  (Phase-0: "aac" or "none")
    encoder: str      # e.g. "libx264"
    crf: Optional[str] = None      # e.g. "28" for preview, "18" for high
    preset: Optional[str] = None   # e.g. "medium" for preview, "slow" for high
    profile: Optional[str] = None  # canonical name: "preview" or "high"


class Producer(BaseModel):
    """Identifies the software that produced this RenderOutput."""
    name: str = "PreviewRenderer"
    version: str = "0.0.1"


class RenderFingerprint(BaseModel):
    """Deterministic render fingerprint — no wall-clock timestamps."""
    inputs_digest: str            # SHA-256 of canonical plan+manifest+effective_settings
    mp4_sha256: str               # SHA-256 of output.mp4 bytes
    srt_sha256: str               # SHA-256 of output.srt text (UTF-8); "" if no VO
    frame_hashes: list[str] = []  # per-frame lines from ffmpeg -f framemd5 (# lines stripped)


class RenderOutput(BaseModel):
    """
    RenderOutput — result of one renderer invocation.
    Canonical schema §5.9. Written to render_output.json alongside the .mp4 and .srt.
    """
    schema_version: str = "0.0.1"
    schema_id: str = "RenderOutput"
    output_id: str
    request_id: str
    render_plan_ref: str
    asset_manifest_ref: str = ""         # file:// absolute path to input AssetManifest
    video_uri: Optional[str] = None      # file:// URI of output .mp4; None in dry-run
    captions_uri: Optional[str] = None  # file:// URI of .srt; null if no VO lines
    audio_stems_uri: Optional[str] = None  # null in Phase 0
    hashes: OutputHashes
    provenance: Provenance
    lineage: Lineage
    outputs: list[OutputArtifact] = []
    effective_settings: Optional[EffectiveSettings] = None
    inputs_digest: str = ""  # SHA-256 of canonical plan+manifest+effective_settings
    producer: Producer = Field(default_factory=Producer)


class RenderAudit(BaseModel):
    """Result of one audit-render invocation."""
    status: str                # "pass" | "fail"
    diff_fields: list[str] = []  # field paths that differed between the two runs
