"""
Cross-repo smoke test: runs scripts/smoke_render.py against the Phase 0
orchestrator artifacts and verifies contract fields in render_output.json.

Requires: ffmpeg on PATH, artifacts at /tmp/orch-artifacts/phase0-demo/run-4aab1f4530ea/
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ARTIFACTS = Path("/tmp/orch-artifacts/phase0-demo/run-4aab1f4530ea")
MANIFEST_PATH = ARTIFACTS / "AssetManifest.json"
PLAN_PATH = ARTIFACTS / "RenderPlan.json"
SMOKE_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "render_from_orchestrator.py"
_EXPECTED_HASH = "12fc3b425b23b76456ebda4a86848ab0da27d0f833a63fdbaeaf1b6f44904b7e"


@pytest.mark.slow
class TestSmokeRender:
    @pytest.fixture(autouse=True)
    def _need_ffmpeg(self, require_ffmpeg):
        """All tests require ffmpeg."""

    @pytest.fixture(autouse=True, scope="class")
    def _need_artifacts(self):
        if not MANIFEST_PATH.exists() or not PLAN_PATH.exists():
            pytest.skip(f"Orchestrator artifacts not found at {ARTIFACTS}")

    @pytest.fixture(scope="class")
    def smoke_out(self, tmp_path_factory):
        """Run smoke_render.py once; return the output directory."""
        out = tmp_path_factory.mktemp("smoke_out")
        result = subprocess.run(
            [
                sys.executable,
                str(SMOKE_SCRIPT),
                "--asset-manifest",
                str(MANIFEST_PATH),
                "--render-plan",
                str(PLAN_PATH),
                "--out-dir",
                str(out),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"smoke_render.py failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
        return out

    def test_mp4_exists(self, smoke_out):
        assert (smoke_out / "output.mp4").exists()

    def test_srt_exists(self, smoke_out):
        assert (smoke_out / "output.srt").exists()

    def test_render_output_json_exists(self, smoke_out):
        assert (smoke_out / "render_output.json").exists()

    def test_timing_lock_hash(self, smoke_out):
        data = json.loads((smoke_out / "render_output.json").read_text())
        assert data["provenance"]["timing_lock_hash"] == _EXPECTED_HASH

    def test_render_plan_ref(self, smoke_out):
        data = json.loads((smoke_out / "render_output.json").read_text())
        expected_ref = f"file://{PLAN_PATH.resolve()}"
        assert data["render_plan_ref"] == expected_ref
