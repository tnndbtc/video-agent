"""
Golden render tests for Phase 0 preview_local renderer.

These tests verify:
  1. Deterministic output — same inputs → bit-identical video (framemd5 match).
  2. Correct duration  — output matches sum of shot durations (±50 ms).
  3. Correct resolution — output matches RenderPlan resolution exactly.
  4. Placeholder handling — shots with no asset produce a placeholder and the
     render still completes (placeholder_count > 0).
  5. SRT generation — output.srt is non-empty and contains expected speaker labels.
  6. RenderOutput contract — render_output.json round-trips through the Pydantic
     model AND validates against RenderOutput.v1.json contract schema.

Requires: ffmpeg >= 6.1 on PATH.
Run:
    cd video/tools
    pytest tests/golden/test_preview_golden.py -v

Regenerate expected hashes after intentional renderer changes:
    python tests/golden/generate_golden.py
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from renderer.preview_local import PreviewRenderer
from schemas.render_output import RenderOutput
from verify_contracts import check_schema, CONTRACTS_DIR as _CONTRACTS_DIR

_SCHEMAS_DIR = _CONTRACTS_DIR / "schemas"

EXPECTED_DIR = Path(__file__).parent / "expected"
GOLDEN_HASH_FILE = EXPECTED_DIR / "render_preview_5shots.framemd5"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_frame_md5(video_path: Path) -> str:
    """
    Extract per-frame MD5 hashes using ffmpeg -f framemd5.

    This hashes decoded frame data and is independent of container metadata
    (creation_time, encoder version strings, etc.), so it is stable across
    re-renders with the same ffmpeg major.minor version.

    Comment lines (starting with '#') are stripped so the comparison is
    insensitive to ffmpeg version comment strings.
    """
    result = subprocess.run(
        ["ffmpeg", "-i", str(video_path), "-f", "framemd5", "-"],
        capture_output=True,
        text=True,
        check=True,
    )
    lines = [ln for ln in result.stdout.splitlines() if not ln.startswith("#")]
    return "\n".join(lines)


def ffprobe_video_info(video_path: Path) -> dict:
    """Return a dict with {duration_sec, width, height, fps} via ffprobe."""
    import json as _json

    r = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_format", "-show_streams",
            str(video_path),
        ],
        capture_output=True, text=True, check=True,
    )
    data = _json.loads(r.stdout)
    fmt = data.get("format", {})
    video_stream = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "video"),
        {},
    )
    fps_str = video_stream.get("r_frame_rate", "0/1")
    num, den = (int(x) for x in fps_str.split("/"))
    fps = num / den if den else 0.0
    return {
        "duration_sec": float(fmt.get("duration", 0)),
        "width": int(video_stream.get("width", 0)),
        "height": int(video_stream.get("height", 0)),
        "fps": fps,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.slow
class TestPreviewGolden:

    @pytest.fixture(autouse=True)
    def _need_ffmpeg(self, require_ffmpeg):
        """All tests in this class require ffmpeg."""

    def test_deterministic_frame_hashes(
        self,
        sample_manifest,
        sample_plan,
        tmp_path: Path,
    ):
        """
        Render the 5-shot fixture twice; both outputs must produce identical
        framemd5 sequences.  Also compare against the committed golden file
        if it exists.
        """
        out_a = tmp_path / "render_a"
        out_b = tmp_path / "render_b"

        PreviewRenderer(sample_manifest, sample_plan, output_dir=out_a).render()
        PreviewRenderer(sample_manifest, sample_plan, output_dir=out_b).render()

        hash_a = get_frame_md5(out_a / "output.mp4")
        hash_b = get_frame_md5(out_b / "output.mp4")

        assert hash_a == hash_b, (
            "Two renders of identical inputs produced different frame hashes.\n"
            "This indicates a non-determinism bug in the renderer."
        )

        # Compare against committed golden file if present.
        if GOLDEN_HASH_FILE.exists():
            expected = GOLDEN_HASH_FILE.read_text().strip()
            assert hash_a == expected, (
                f"Frame hash mismatch vs. golden file {GOLDEN_HASH_FILE}.\n"
                f"If this is intentional, regenerate:\n"
                f"    python tests/golden/generate_golden.py\n\n"
                f"Expected first 3 lines:\n"
                + "\n".join(expected.splitlines()[:3])
                + "\n\nActual first 3 lines:\n"
                + "\n".join(hash_a.splitlines()[:3])
            )
        else:
            pytest.skip(
                f"Golden hash file not found: {GOLDEN_HASH_FILE}\n"
                "Run `python tests/golden/generate_golden.py` to create it."
            )

    def test_output_duration(self, sample_manifest, sample_plan, tmp_path: Path):
        """Output video duration must match the sum of shot durations (±50 ms)."""
        out = tmp_path / "render_dur"
        PreviewRenderer(sample_manifest, sample_plan, output_dir=out).render()

        expected_ms = sum(s.duration_ms for s in sample_manifest.shots)
        info = ffprobe_video_info(out / "output.mp4")
        actual_ms = int(info["duration_sec"] * 1000)

        assert abs(actual_ms - expected_ms) <= 50, (
            f"Duration mismatch: expected {expected_ms} ms, got {actual_ms} ms"
        )

    def test_output_resolution(self, sample_manifest, sample_plan, tmp_path: Path):
        """Output resolution must match RenderPlan exactly."""
        out = tmp_path / "render_res"
        PreviewRenderer(sample_manifest, sample_plan, output_dir=out).render()

        info = ffprobe_video_info(out / "output.mp4")
        assert info["width"] == sample_plan.resolution.width
        assert info["height"] == sample_plan.resolution.height

    def test_placeholder_shot_does_not_abort_render(
        self, sample_manifest, sample_plan, tmp_path: Path
    ):
        """
        shot_004 has no visual asset; the render must complete and report
        placeholder_count >= 1.
        """
        out = tmp_path / "render_ph"
        result = PreviewRenderer(sample_manifest, sample_plan, output_dir=out).render()

        assert (out / "output.mp4").exists()
        assert result.provenance.placeholder_count >= 1

    def test_srt_captions_generated(self, sample_manifest, sample_plan, tmp_path: Path):
        """output.srt must exist and contain the expected speaker labels."""
        out = tmp_path / "render_srt"
        PreviewRenderer(sample_manifest, sample_plan, output_dir=out).render()

        srt_path = out / "output.srt"
        assert srt_path.exists()
        content = srt_path.read_text(encoding="utf-8")
        assert "narrator:" in content   # speaker_id preserved as-is (no .upper())
        assert "Hello world" in content
        assert "Goodbye" in content

    def test_render_output_json_schema_valid(
        self, sample_manifest, sample_plan, tmp_path: Path
    ):
        """render_output.json must round-trip through the Pydantic model AND pass
        the canonical RenderOutput.v1.json contract schema."""
        out = tmp_path / "render_json"
        result = PreviewRenderer(sample_manifest, sample_plan, output_dir=out).render()

        json_path = out / "render_output.json"
        assert json_path.exists()

        # Re-parse from disk and verify Pydantic-level fields.
        from_disk = RenderOutput.model_validate_json(json_path.read_text())
        assert from_disk.lineage.asset_manifest_hash == result.lineage.asset_manifest_hash
        assert from_disk.hashes.video_sha256 == result.hashes.video_sha256
        assert from_disk.provenance.render_profile == "preview"
        assert from_disk.schema_version == "0.0.1"
        assert from_disk.schema_id == "RenderOutput"
        assert from_disk.producer.name == "PreviewRenderer"

        # Validate the on-disk JSON against the canonical contract schema.
        data = json.loads(json_path.read_text())
        errors = check_schema(data, "RenderOutput", _SCHEMAS_DIR)
        assert errors == [], (
            "render_output.json failed RenderOutput.v1.json contract:\n"
            + "\n".join(errors)
        )

    def test_timing_lock_hash_mismatch_raises(self, sample_manifest, tmp_path: Path):
        """Mismatched timing_lock_hash must raise ValueError before any ffmpeg call."""
        from schemas.render_plan import RenderPlan

        bad_plan = RenderPlan(
            plan_id="bad",
            project_id=sample_manifest.project_id,
            asset_manifest_ref="file:///m.json",
            timing_lock_hash="sha256:WRONG-HASH",  # deliberately wrong
        )
        with pytest.raises(ValueError, match="timing_lock_hash mismatch"):
            PreviewRenderer(sample_manifest, bad_plan, output_dir=tmp_path / "x")
