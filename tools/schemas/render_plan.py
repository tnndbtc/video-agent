"""
RenderPlan — canonical schema §5.8 of master plan.

Maps AssetManifest to actual sources + render profile:
  - profile: preview_local | standard_local | hq_providerX
  - resolution / aspect / fps
  - resolved asset URIs + fallbacks
  - timing lock hash reference (from ShotList, must match AssetManifest)

Phase 0 only supports profile=preview_local.
asset_resolutions maps asset_id → resolved file URI.
Missing entries in asset_resolutions trigger placeholder generation.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class Resolution(BaseModel):
    """Output resolution. Canonical field names: width, height, aspect."""
    width: int = 1280
    height: int = 720
    aspect: str = "16:9"


class FallbackConfig(BaseModel):
    """
    Fallback / placeholder configuration for missing assets (§5.8: resolved asset URIs + fallbacks).
    placeholder_font_path: absolute path to a .ttf font on the render host.
    Default points to DejaVuSans on Ubuntu; override for other environments.
    """
    placeholder_color: str = "#1a1a2e"
    placeholder_font_path: str = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    placeholder_font_size: int = 36


class RenderPlan(BaseModel):
    """
    RenderPlan — resolves AssetManifest to render-ready inputs with profile.
    Canonical schema §5.8.

    asset_resolutions: {asset_id: file_uri} — built by the asset resolver (§9 / Workstream TBD).
    audio_resolutions: {line_id: file_uri}  — resolved TTS / SFX audio files.
    Entries absent from these dicts → renderer falls back to placeholder / silence.

    timing_lock_hash MUST equal AssetManifest.timing_lock_hash; the renderer
    validates this at startup and aborts if they differ.
    """
    schema_version: str = "1.0.0"
    plan_id: str
    project_id: str
    profile: str = "preview_local"           # §5.8: preview_local | standard_local | hq_providerX
    resolution: Resolution = Field(default_factory=Resolution)
    fps: int = 24
    asset_manifest_ref: str                  # URI of the source AssetManifest artifact
    timing_lock_hash: str                    # must match AssetManifest.timing_lock_hash (§5.8)
    asset_resolutions: dict[str, str] = Field(default_factory=dict)
    audio_resolutions: dict[str, str] = Field(default_factory=dict)
    fallback: FallbackConfig = Field(default_factory=FallbackConfig)
