"""
Shared pytest fixtures for video/tools/tests/.

Provides:
  - deterministic test PNG assets (generated with Pillow, not committed binaries)
  - pre-built AssetManifest and RenderPlan objects for the 5-shot golden fixture
  - require_ffmpeg: skip-marker for tests that need the ffmpeg binary
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Iterator

import pytest

# ---- optional Pillow import ----
try:
    from PIL import Image
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False


# ---------------------------------------------------------------------------
# Constants shared across unit and golden tests
# ---------------------------------------------------------------------------

TIMING_LOCK_HASH = "sha256:test-timing-lock-abc123"

# Deterministic solid-color test images (decoded RGB is identical across runs).
# Sorted by shot index so they are always assigned to the same shot.
_ASSET_COLORS: dict[str, tuple[int, int, int]] = {
    "shot_001": (200, 60,  60),   # red
    "shot_002": (60,  200, 60),   # green
    "shot_003": (60,  60,  200),  # blue
}

_ASSET_SHOT_DURATION_MS = 2_000   # 2 s per shot
_W, _H = 1280, 720
_FPS = 24


# ---------------------------------------------------------------------------
# Test-asset generation (deterministic with Pillow)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def test_assets_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """
    Create three solid-colour PNG files in a session-scoped temp directory.

    The PNG content is determined solely by (width, height, RGB) — identical
    across Pillow versions for solid-colour images — so the decoded frames
    that ffmpeg processes are bit-stable.
    """
    if not _PIL_AVAILABLE:
        pytest.skip("Pillow not installed; cannot generate test assets.")

    assets_dir = tmp_path_factory.mktemp("assets", numbered=False)
    for name, color in _ASSET_COLORS.items():
        img = Image.new("RGB", (_W, _H), color=color)
        img.save(str(assets_dir / f"{name}.png"), format="PNG",
                 compress_level=9, optimize=False)
    return assets_dir


# ---------------------------------------------------------------------------
# Manifest / plan fixture builders
# ---------------------------------------------------------------------------

def build_manifest(assets_dir: Path):
    """
    Build the canonical 5-shot AssetManifest used by unit and golden tests.

    Shot layout:
      shot_001  2 s  bg=shot_001.png   VO: "Hello world"  (0–1800 ms)
      shot_002  2 s  bg=shot_002.png   no VO
      shot_003  2 s  bg=shot_003.png   no VO
      shot_004  2 s  no asset → placeholder
      shot_005  2 s  bg=shot_001.png   VO: "Goodbye"  (200–1600 ms)
    """
    from schemas.asset_manifest import (
        AssetManifest,
        Shot,
        VisualAsset,
        VOLine,
    )

    def img_uri(name: str) -> str:
        return f"file://{(assets_dir / (name + '.png')).resolve()}"

    shots = [
        Shot(
            shot_id="shot_001",
            duration_ms=_ASSET_SHOT_DURATION_MS,
            visual_assets=[
                VisualAsset(
                    asset_id="bg_001",
                    role="background",
                    asset_uri=img_uri("shot_001"),
                )
            ],
            vo_lines=[
                VOLine(
                    line_id="vo_001",
                    speaker_id="narrator",
                    text="Hello world",
                    emotion="neutral",
                    timeline_in_ms=0,
                    timeline_out_ms=1_800,
                )
            ],
        ),
        Shot(
            shot_id="shot_002",
            duration_ms=_ASSET_SHOT_DURATION_MS,
            visual_assets=[
                VisualAsset(
                    asset_id="bg_002",
                    role="background",
                    asset_uri=img_uri("shot_002"),
                )
            ],
        ),
        Shot(
            shot_id="shot_003",
            duration_ms=_ASSET_SHOT_DURATION_MS,
            visual_assets=[
                VisualAsset(
                    asset_id="bg_003",
                    role="background",
                    asset_uri=img_uri("shot_003"),
                )
            ],
        ),
        Shot(
            shot_id="shot_004",
            duration_ms=_ASSET_SHOT_DURATION_MS,
            # No visual_assets → renderer must generate placeholder.
        ),
        Shot(
            shot_id="shot_005",
            duration_ms=_ASSET_SHOT_DURATION_MS,
            visual_assets=[
                VisualAsset(
                    asset_id="bg_005",
                    role="background",
                    asset_uri=img_uri("shot_001"),   # re-use shot_001 colour
                )
            ],
            vo_lines=[
                VOLine(
                    line_id="vo_002",
                    speaker_id="narrator",
                    text="Goodbye",
                    emotion="neutral",
                    timeline_in_ms=200,
                    timeline_out_ms=1_600,
                )
            ],
        ),
    ]

    return AssetManifest(
        manifest_id="test-manifest-5shots",
        project_id="test-project",
        shotlist_ref="file:///test/shotlist.json",
        timing_lock_hash=TIMING_LOCK_HASH,
        shots=shots,
    )


def build_plan(assets_dir: Path):
    """Build the matching RenderPlan for the 5-shot manifest."""
    from schemas.render_plan import FallbackConfig, RenderPlan, Resolution

    return RenderPlan(
        plan_id="test-plan-5shots",
        project_id="test-project",
        profile="preview_local",
        resolution=Resolution(width=_W, height=_H, aspect="16:9"),
        fps=_FPS,
        asset_manifest_ref="file:///test/asset_manifest.json",
        timing_lock_hash=TIMING_LOCK_HASH,
        # asset_resolutions is intentionally empty → renderer uses asset_uri
        # from the manifest directly, which exercises the fallback code path.
        fallback=FallbackConfig(
            placeholder_color="#1a1a2e",
            placeholder_font_path="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            placeholder_font_size=36,
        ),
    )


@pytest.fixture(scope="session")
def sample_manifest(test_assets_dir: Path):
    return build_manifest(test_assets_dir)


@pytest.fixture(scope="session")
def sample_plan(test_assets_dir: Path):
    return build_plan(test_assets_dir)


# ---------------------------------------------------------------------------
# FFmpeg availability check
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def require_ffmpeg():
    """Skip the test if ffmpeg is not available on PATH."""
    result = subprocess.run(
        ["ffmpeg", "-version"], capture_output=True, timeout=5
    )
    if result.returncode != 0:
        pytest.skip("ffmpeg not available — skipping render test.")
