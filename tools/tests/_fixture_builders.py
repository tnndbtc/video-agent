"""Shared minimal fixture for verify-mode tests and the video CLI."""
from __future__ import annotations

from schemas.asset_manifest import AssetManifest, Shot
from schemas.render_plan import FallbackConfig, RenderPlan, Resolution

_TIMING = "sha256:test-timing-lock-abc123"


def build_minimal_verify_fixture(profile: str = "preview_local") -> tuple[AssetManifest, RenderPlan]:
    """Return (manifest, plan) for the canonical dry-m / dry-pl minimal fixture."""
    manifest = AssetManifest(
        manifest_id="dry-m", project_id="dry-p",
        shotlist_ref="file:///sl.json", timing_lock_hash=_TIMING,
        shots=[Shot(shot_id="s1", duration_ms=2_000)],
    )
    plan = RenderPlan(
        plan_id="dry-pl", project_id="dry-p", profile=profile,
        resolution=Resolution(width=1280, height=720, aspect="16:9"),
        fps=24, asset_manifest_ref="file:///render_plan.json",
        timing_lock_hash=_TIMING, fallback=FallbackConfig(),
    )
    return manifest, plan
