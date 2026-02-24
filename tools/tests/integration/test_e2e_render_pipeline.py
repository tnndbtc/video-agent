"""
End-to-end integration test for the full render pipeline using
AssetManifest.final.json (flat items[] format) + orchestrator RenderPlan.json.

Exercises the _adapt_manifest_final + _adapt_plan code paths in tools/cli.py —
the code path that the orchestrator hits in production.

  - Builds a valid AssetManifest.final.json (schema_id = "AssetManifest_final",
    two background shots + one VO item, all fields per the contract schema).
  - Builds a valid RenderPlan.json (schema_id = "RenderPlan", orchestrator
    format with resolved_assets[]).
  - Calls `video render` via subprocess; outputs land in
    /tmp/video-agent-e2e-<timestamp>/.
  - Prints the exact command run and `ls -lh` of the output directory.
  - Asserts RenderOutput.json, output.mp4, output.srt exist with correct hashes.
  - Cleans up the /tmp output directory after all tests finish.

TestE2ERenderPipeline  — @pytest.mark.slow (requires ffmpeg)
TestE2EDryRunPipeline  — no ffmpeg required (fast contract + adapter smoke test)
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

VIDEO_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "video.py"

TIMING_LOCK_HASH = "sha256:test-timing-lock-abc123"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65_536), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _resolved_asset(
    asset_id: str,
    asset_type: str,
    uri: str,
    *,
    is_placeholder: bool = False,
    source_type: str = "local",
    license_type: str = "CC0",
    spdx_id: str = "CC0-1.0",
) -> dict:
    """Return a dict conforming to the ResolvedAsset sub-schema."""
    return {
        "asset_id": asset_id,
        "asset_type": asset_type,
        "uri": uri,
        "is_placeholder": is_placeholder,
        "metadata": {
            "license_type": license_type,
            # MUST be epoch sentinel for local/cached assets (schema §25.2).
            "retrieval_date": "1970-01-01T00:00:00Z",
        },
        "source": {"type": source_type},
        "license": {"spdx_id": spdx_id, "attribution_required": False},
        # Per-item type discriminator required by the schema.
        "schema_id": "urn:media:resolved-asset",
        "schema_version": "1.0.0",
        "producer": "test-e2e-fixture",
    }


# ---------------------------------------------------------------------------
# Shared fixture: build AssetManifest.final.json + RenderPlan.json on disk
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def e2e_fixture_files(tmp_path_factory, test_assets_dir):
    """Write AssetManifest.final.json and RenderPlan.json for the E2E tests.

    Uses the session-scoped test PNG assets (shot_001=red, shot_002=green)
    so the fixture depends only on Pillow, not on ffmpeg.
    """
    # Resolve real file URIs for the two background PNG assets.
    bg1_uri = f"file://{(test_assets_dir / 'shot_001.png').resolve()}"
    bg2_uri = f"file://{(test_assets_dir / 'shot_002.png').resolve()}"

    # ── AssetManifest.final.json ──────────────────────────────────────────────
    # schema_id = "AssetManifest_final"  →  validated against AssetManifest_final.v1.json
    # Items drive _adapt_manifest_final:
    #   bg-scene-001  → scene_id = "scene-001"
    #   bg-scene-002  → scene_id = "scene-002"
    #   vo-scene-001-* asset_id contains "scene-001" → attached to shot scene-001
    manifest = {
        "schema_id": "AssetManifest_final",
        "schema_version": "1.0.0",
        "manifest_id": "e2e-test-manifest-final-001",
        "project_id": "e2e-test-project",
        "shotlist_ref": "file:///test/shotlist.json",
        "items": [
            _resolved_asset(
                asset_id="bg-scene-001",
                asset_type="background",
                uri=bg1_uri,
            ),
            _resolved_asset(
                asset_id="bg-scene-002",
                asset_type="background",
                uri=bg2_uri,
            ),
            # VO item associated with scene-001 (scene_id "scene-001" is a
            # substring of the asset_id, per _adapt_manifest_final logic).
            _resolved_asset(
                asset_id="vo-scene-001-narrator-01",
                asset_type="vo",
                uri="placeholder://vo/vo-scene-001",
                is_placeholder=True,
                source_type="generated_placeholder",
                spdx_id="NOASSERTION",
            ),
        ],
    }

    # ── RenderPlan.json (orchestrator format) ─────────────────────────────────
    # schema_id = "RenderPlan"  →  validated against RenderPlan.v1.json
    # resolution is a "WxH" string; _adapt_plan splits it.
    plan = {
        "schema_id": "RenderPlan",
        "schema_version": "1.0.0",
        "plan_id": "e2e-test-plan-001",
        "project_id": "e2e-test-project",
        "manifest_ref": "file:///test/AssetManifest.final.json",
        "timing_lock_hash": TIMING_LOCK_HASH,
        "profile": "preview_local",
        "resolution": "1280x720",
        "aspect_ratio": "16:9",
        "fps": 24,
        "resolved_assets": [
            {
                "asset_id": "bg-scene-001",
                "asset_type": "background",
                "uri": bg1_uri,
                "license_type": "CC0",
                "is_placeholder": False,
            },
            {
                "asset_id": "bg-scene-002",
                "asset_type": "background",
                "uri": bg2_uri,
                "license_type": "CC0",
                "is_placeholder": False,
            },
        ],
    }

    fixture_dir = tmp_path_factory.mktemp("e2e_input_fixtures")
    manifest_path = fixture_dir / "AssetManifest.final.json"
    plan_path = fixture_dir / "RenderPlan.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    return manifest_path, plan_path


# ---------------------------------------------------------------------------
# Full render (requires ffmpeg)
# ---------------------------------------------------------------------------

@pytest.mark.slow
class TestE2ERenderPipeline:
    """Full pipeline E2E test: AssetManifest.final + RenderPlan → mp4+srt+RenderOutput."""

    @pytest.fixture(autouse=True)
    def _need_ffmpeg(self, require_ffmpeg): ...

    @pytest.fixture(scope="class")
    def e2e_render_out(self, e2e_fixture_files):
        manifest_path, plan_path = e2e_fixture_files

        # Write outputs to a timestamped /tmp directory so sizes are visible.
        ts = time.strftime("%Y%m%d-%H%M%S")
        out_dir = Path(f"/tmp/video-agent-e2e-{ts}")
        out_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            sys.executable, str(VIDEO_SCRIPT), "render",
            "--manifest", str(manifest_path),
            "--plan",     str(plan_path),
            "--out",      str(out_dir / "RenderOutput.json"),
            "--video",    str(out_dir / "output.mp4"),
            "--srt",      str(out_dir / "output.srt"),
        ]

        print("\n")
        print("=" * 70)
        print("  E2E Render Pipeline — AssetManifest.final.json format")
        print("=" * 70)
        print(f"\n  Output directory: {out_dir}\n")
        print("  Command:")
        print(f"    {' '.join(cmd)}\n")

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.stderr.strip():
            print("  STDERR:")
            print("  " + result.stderr.strip()[:600].replace("\n", "\n  "))

        try:
            assert result.returncode == 0, (
                f"video render exited {result.returncode}:\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            )

            # Show output file sizes (ls -lh).
            ls_result = subprocess.run(
                ["ls", "-lh", str(out_dir)],
                capture_output=True, text=True,
            )
            print("\n  Output files (ls -lh):")
            for line in ls_result.stdout.splitlines():
                print(f"    {line}")
            print()

            yield {"out_dir": out_dir, "stdout": result.stdout}

        finally:
            shutil.rmtree(str(out_dir), ignore_errors=True)
            print(f"\n  Cleaned up: {out_dir}\n")

    # ── assertions ────────────────────────────────────────────────────────────

    def test_render_output_json_exists(self, e2e_render_out):
        assert (e2e_render_out["out_dir"] / "RenderOutput.json").exists()

    def test_mp4_exists(self, e2e_render_out):
        assert (e2e_render_out["out_dir"] / "output.mp4").exists()

    def test_srt_exists(self, e2e_render_out):
        assert (e2e_render_out["out_dir"] / "output.srt").exists()

    def test_stdout_is_valid_render_output_json(self, e2e_render_out):
        data = json.loads(e2e_render_out["stdout"])
        assert data["schema_version"] == "0.0.1"
        assert "output_id" in data
        assert "hashes" in data

    def test_video_sha256_matches_file(self, e2e_render_out):
        data = json.loads(
            (e2e_render_out["out_dir"] / "RenderOutput.json").read_text(encoding="utf-8")
        )
        actual = _sha256_file(e2e_render_out["out_dir"] / "output.mp4")
        assert data["hashes"]["video_sha256"] == actual

    def test_captions_sha256_matches_file(self, e2e_render_out):
        data = json.loads(
            (e2e_render_out["out_dir"] / "RenderOutput.json").read_text(encoding="utf-8")
        )
        actual = _sha256_text(
            (e2e_render_out["out_dir"] / "output.srt").read_text(encoding="utf-8")
        )
        assert data["hashes"]["captions_sha256"] == actual

    def test_effective_settings_present(self, e2e_render_out):
        data = json.loads(
            (e2e_render_out["out_dir"] / "RenderOutput.json").read_text(encoding="utf-8")
        )
        assert "effective_settings" in data

    def test_two_shots_in_manifest_yielded_output(self, e2e_render_out):
        """Two bg items in the manifest must produce a non-empty mp4."""
        mp4 = e2e_render_out["out_dir"] / "output.mp4"
        assert mp4.stat().st_size > 0


# ---------------------------------------------------------------------------
# Dry-run variant: no ffmpeg required
# ---------------------------------------------------------------------------

class TestE2EDryRunPipeline:
    """Dry-run E2E: validates AssetManifest.final contract + adapter; no mp4/srt."""

    @pytest.fixture(scope="class")
    def dry_run_out(self, tmp_path_factory, e2e_fixture_files):
        manifest_path, plan_path = e2e_fixture_files
        out_dir = tmp_path_factory.mktemp("e2e_dry_run_out")

        cmd = [
            sys.executable, str(VIDEO_SCRIPT), "render",
            "--manifest", str(manifest_path),
            "--plan",     str(plan_path),
            "--out",      str(out_dir / "RenderOutput.json"),
            "--video",    str(out_dir / "output.mp4"),
            "--dry-run",
        ]

        print("\n")
        print("=" * 70)
        print("  E2E Dry-Run — AssetManifest.final.json format (no ffmpeg)")
        print("=" * 70)
        print("\n  Command:")
        print(f"    {' '.join(cmd)}\n")

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.stderr.strip():
            print("  STDERR:")
            print("  " + result.stderr.strip()[:400].replace("\n", "\n  "))

        assert result.returncode == 0, (
            f"video render --dry-run exited {result.returncode}:\n{result.stderr}"
        )
        return {"out_dir": out_dir, "stdout": result.stdout}

    def test_render_output_json_written(self, dry_run_out):
        assert (dry_run_out["out_dir"] / "RenderOutput.json").exists()

    def test_mp4_not_produced(self, dry_run_out):
        assert not (dry_run_out["out_dir"] / "output.mp4").exists()

    def test_srt_not_produced(self, dry_run_out):
        assert not (dry_run_out["out_dir"] / "output.srt").exists()

    def test_stdout_contains_schema_version(self, dry_run_out):
        data = json.loads(dry_run_out["stdout"])
        assert "schema_version" in data

    def test_stdout_contains_effective_settings(self, dry_run_out):
        data = json.loads(dry_run_out["stdout"])
        assert "effective_settings" in data

    def test_stdout_contains_output_id(self, dry_run_out):
        """output_id confirms the adapter pipeline ran end-to-end."""
        data = json.loads(dry_run_out["stdout"])
        assert "output_id" in data
