"""
AssetManifest — canonical schema §5.7 of master plan.

Derived requirements from ShotList:
  - visual assets per shot (character packs, backgrounds, props)
  - VO line items (speaker_id, text, emotion/pacing tags)
  - SFX / music needs
  - No provider references

All asset_uri values use file:// URIs (local Phase 0) or null (placeholder needed).
timing_lock_hash is the SHA-256 hash produced by the ShotList adapter (Workstream B)
and must be preserved unchanged through RenderPlan into the renderer.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class VOLine(BaseModel):
    """
    Voice-over line item. Field names are canonical (§5.7).
    speaker_id, text, emotion, pacing_tags are explicit in the spec.
    """
    line_id: str
    speaker_id: str
    text: str
    emotion: str = ""
    pacing_tags: list[str] = Field(default_factory=list)
    # audio_uri: resolved TTS audio file; null if TTS not yet generated.
    # timeline_in/out_ms: offset from the shot's start (0 if no timing data yet).
    audio_uri: Optional[str] = None
    timeline_in_ms: int = 0
    timeline_out_ms: int = 0


class VisualAsset(BaseModel):
    """
    A visual asset slot required by one shot.
    role values: "background" | "character" | "prop"  (§5.7: character packs, backgrounds, props)
    asset_uri is null when the asset has not been resolved; renderer generates a placeholder.
    """
    asset_id: str
    role: str = "background"
    asset_uri: Optional[str] = None          # file:// URI or null
    license_type: str = "proprietary_cleared"  # §25.2 minimum metadata
    placeholder: bool = False                # true when asset is synthetic / generated


class SFXItem(BaseModel):
    """Sound effect requirement for a shot (§5.7: SFX/music needs)."""
    sfx_id: str
    description: str
    audio_uri: Optional[str] = None
    timeline_in_ms: int = 0


class Shot(BaseModel):
    """
    Per-shot asset requirements, derived from ShotList (§5.6).
    duration_ms is inherited from the ShotList timing lock and must not be altered
    after the timing_lock_hash is produced.
    """
    shot_id: str
    duration_ms: int                             # locked from ShotList §5.6
    visual_assets: list[VisualAsset] = Field(default_factory=list)
    vo_lines: list[VOLine] = Field(default_factory=list)
    sfx: list[SFXItem] = Field(default_factory=list)
    music_mood: str = ""


class AssetManifest(BaseModel):
    """
    AssetManifest — maps ShotList requirements to asset slots.
    Canonical schema §5.7. Consumer: RenderPlan builder (resolves URIs),
    Preview Renderer (renders each shot).

    timing_lock_hash is produced by the ShotList adapter and must match
    the value in the corresponding RenderPlan exactly; any mismatch causes
    the renderer to abort (§10.1 deterministic requirement).
    """
    schema_version: str = "1.0.0"
    manifest_id: str
    project_id: str
    shotlist_ref: str           # URI of the source ShotList artifact
    timing_lock_hash: str       # SHA-256 hash from ShotList timing lock (§5.8)
    shots: list[Shot]
    music_uri: Optional[str] = None  # optional background track URI (§19.0 audio)
