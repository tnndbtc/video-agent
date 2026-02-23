#!/usr/bin/env python3
"""
Generate / update expected frame-hash files for golden render tests.

Run this script once to create the initial golden file, and again whenever
a deliberate renderer change alters the output bitstream.

Usage (from video/tools/ directory):
    python tests/golden/generate_golden.py

Requirements:
    - ffmpeg >= 6.1 on PATH
    - Pillow  >= 10 installed
    - PYTHONPATH must include video/tools/  (set automatically when run via this script)

Output:
    tests/golden/expected/render_preview_5shots.framemd5

After running, verify the output looks correct and commit the .framemd5 file.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

# Ensure video/tools/ is on sys.path when run directly.
_TOOLS_ROOT = Path(__file__).resolve().parents[2]  # video/tools/
if str(_TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(_TOOLS_ROOT))

EXPECTED_DIR = Path(__file__).parent / "expected"
HASH_FILE = EXPECTED_DIR / "render_preview_5shots.framemd5"

_W, _H = 1280, 720
_FPS = 24
_SHOT_DUR_MS = 2_000
TIMING_LOCK_HASH = "sha256:test-timing-lock-abc123"

_ASSET_COLORS: dict[str, tuple[int, int, int]] = {
    "shot_001": (200, 60,  60),
    "shot_002": (60,  200, 60),
    "shot_003": (60,  60,  200),
}


def _make_test_assets(assets_dir: Path) -> None:
    """Generate deterministic solid-colour test PNG assets."""
    from PIL import Image
    assets_dir.mkdir(parents=True, exist_ok=True)
    for name, color in _ASSET_COLORS.items():
        path = assets_dir / f"{name}.png"
        img = Image.new("RGB", (_W, _H), color=color)
        img.save(str(path), format="PNG", compress_level=9, optimize=False)
        print(f"  wrote {path}")


def _build_manifest(assets_dir: Path):
    from schemas.asset_manifest import AssetManifest, Shot, VisualAsset, VOLine

    def uri(name: str) -> str:
        return f"file://{(assets_dir / (name + '.png')).resolve()}"

    return AssetManifest(
        manifest_id="test-manifest-5shots",
        project_id="test-project",
        shotlist_ref="file:///test/shotlist.json",
        timing_lock_hash=TIMING_LOCK_HASH,
        shots=[
            Shot(
                shot_id="shot_001",
                duration_ms=_SHOT_DUR_MS,
                visual_assets=[VisualAsset(asset_id="bg_001", role="background",
                                           asset_uri=uri("shot_001"))],
                vo_lines=[VOLine(line_id="vo_001", speaker_id="narrator",
                                 text="Hello world", emotion="neutral",
                                 timeline_in_ms=0, timeline_out_ms=1_800)],
            ),
            Shot(
                shot_id="shot_002",
                duration_ms=_SHOT_DUR_MS,
                visual_assets=[VisualAsset(asset_id="bg_002", role="background",
                                           asset_uri=uri("shot_002"))],
            ),
            Shot(
                shot_id="shot_003",
                duration_ms=_SHOT_DUR_MS,
                visual_assets=[VisualAsset(asset_id="bg_003", role="background",
                                           asset_uri=uri("shot_003"))],
            ),
            Shot(
                shot_id="shot_004",
                duration_ms=_SHOT_DUR_MS,
                # no visual_assets → placeholder generated
            ),
            Shot(
                shot_id="shot_005",
                duration_ms=_SHOT_DUR_MS,
                visual_assets=[VisualAsset(asset_id="bg_005", role="background",
                                           asset_uri=uri("shot_001"))],
                vo_lines=[VOLine(line_id="vo_002", speaker_id="narrator",
                                 text="Goodbye", emotion="neutral",
                                 timeline_in_ms=200, timeline_out_ms=1_600)],
            ),
        ],
    )


def _build_plan():
    from schemas.render_plan import FallbackConfig, RenderPlan, Resolution

    return RenderPlan(
        plan_id="test-plan-5shots",
        project_id="test-project",
        profile="preview_local",
        resolution=Resolution(width=_W, height=_H, aspect="16:9"),
        fps=_FPS,
        asset_manifest_ref="file:///test/asset_manifest.json",
        timing_lock_hash=TIMING_LOCK_HASH,
        fallback=FallbackConfig(
            placeholder_color="#1a1a2e",
            placeholder_font_path="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            placeholder_font_size=36,
        ),
    )


def _extract_frame_md5(video_path: Path) -> str:
    result = subprocess.run(
        ["ffmpeg", "-i", str(video_path), "-f", "framemd5", "-"],
        capture_output=True, text=True, check=True,
    )
    lines = [ln for ln in result.stdout.splitlines() if not ln.startswith("#")]
    return "\n".join(lines)


def main() -> None:
    print("Phase 0 golden hash generator")
    print(f"  output: {HASH_FILE}")
    print()

    # Check ffmpeg.
    try:
        from renderer.ffmpeg_runner import validate_ffmpeg
        version = validate_ffmpeg()
        print(f"  ffmpeg version: {version}")
    except Exception as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)

    with tempfile.TemporaryDirectory(prefix="golden_gen_") as tmp:
        tmp_path = Path(tmp)
        assets_dir = tmp_path / "assets"
        render_dir = tmp_path / "render"

        print("Generating test assets ...")
        _make_test_assets(assets_dir)

        print("Building manifest + plan ...")
        manifest = _build_manifest(assets_dir)
        plan = _build_plan()

        print(f"Rendering {len(manifest.shots)} shots ...")
        from renderer.preview_local import PreviewRenderer
        result = PreviewRenderer(manifest, plan, output_dir=render_dir).render()
        output_mp4 = render_dir / "output.mp4"

        size = output_mp4.stat().st_size
        print(f"  output: {output_mp4}  ({size:,} bytes)")
        print(f"  placeholder_count: {result.provenance.placeholder_count}")
        print(f"  video SHA-256: {result.hashes.video_sha256[:16]}...")

        print("Extracting frame hashes ...")
        frame_md5 = _extract_frame_md5(output_mp4)
        n_frames = len(frame_md5.strip().splitlines())
        print(f"  {n_frames} frames hashed")

    # Write to expected/ directory.
    EXPECTED_DIR.mkdir(parents=True, exist_ok=True)
    HASH_FILE.write_text(frame_md5, encoding="utf-8")
    print(f"\nWrote {HASH_FILE}")
    print("First 3 lines:")
    for ln in frame_md5.splitlines()[:3]:
        print(f"  {ln}")

    print("\nDone. Review the file and commit it if correct.")
    print("  git add tests/golden/expected/render_preview_5shots.framemd5")


if __name__ == "__main__":
    main()
