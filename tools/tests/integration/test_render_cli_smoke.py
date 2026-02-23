"""
Self-contained smoke test for scripts/smoke_render.py.

Uses the golden fixture builders (build_manifest / build_plan via sample_manifest
/ sample_plan) to produce native-format JSON — no external orchestrator artifacts
required.

Marked @pytest.mark.slow (requires ffmpeg). The CLI is invoked once per test
class via the class-scoped ``cli_out`` fixture.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

SMOKE_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "render_from_orchestrator.py"


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


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

@pytest.mark.slow
class TestRenderCliSmoke:
    """Run smoke_render.py with native-format fixtures and assert contract fields."""

    @pytest.fixture(autouse=True)
    def _need_ffmpeg(self, require_ffmpeg):
        """All tests in this class require ffmpeg."""

    @pytest.fixture(scope="class")
    def cli_out(self, tmp_path_factory, sample_manifest, sample_plan):
        """
        Serialise native-format JSON files, invoke smoke_render.py once, and
        return a dict with:
          out_dir  — Path to the render output directory
          stdout   — captured stdout string from the CLI
          plan     — the RenderPlan object (for hash assertions)
        """
        run_dir = tmp_path_factory.mktemp("cli_smoke_run", numbered=True)
        manifest_path = run_dir / "AssetManifest.json"
        plan_path = run_dir / "RenderPlan.json"

        manifest_path.write_text(sample_manifest.model_dump_json(), encoding="utf-8")
        plan_path.write_text(sample_plan.model_dump_json(), encoding="utf-8")

        out_dir = tmp_path_factory.mktemp("cli_smoke_out", numbered=True)

        result = subprocess.run(
            [
                sys.executable,
                str(SMOKE_SCRIPT),
                "--asset-manifest", str(manifest_path),
                "--render-plan", str(plan_path),
                "--out-dir", str(out_dir),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"smoke_render.py exited with code {result.returncode}:\n"
            f"STDOUT:\n{result.stdout}\n"
            f"STDERR:\n{result.stderr}"
        )
        return {
            "out_dir": out_dir,
            "stdout": result.stdout,
            "plan": sample_plan,
            "manifest_path": manifest_path,
        }

    # -----------------------------------------------------------------------
    # Output artefact existence
    # -----------------------------------------------------------------------

    def test_mp4_exists(self, cli_out):
        assert (cli_out["out_dir"] / "output.mp4").exists()

    def test_srt_exists(self, cli_out):
        assert (cli_out["out_dir"] / "output.srt").exists()

    def test_render_output_json_exists(self, cli_out):
        assert (cli_out["out_dir"] / "render_output.json").exists()

    # -----------------------------------------------------------------------
    # Stdout contract
    # -----------------------------------------------------------------------

    def test_stdout_is_valid_json(self, cli_out):
        data = json.loads(cli_out["stdout"])
        assert data["schema_version"] == "0.0.1"
        assert "output_id" in data

    # -----------------------------------------------------------------------
    # Hash integrity
    # -----------------------------------------------------------------------

    def test_video_sha256_matches(self, cli_out):
        data = json.loads((cli_out["out_dir"] / "render_output.json").read_text())
        actual = _sha256_file(cli_out["out_dir"] / "output.mp4")
        assert data["hashes"]["video_sha256"] == actual

    def test_captions_sha256_matches(self, cli_out):
        data = json.loads((cli_out["out_dir"] / "render_output.json").read_text())
        actual = _sha256_text(
            (cli_out["out_dir"] / "output.srt").read_text(encoding="utf-8")
        )
        assert data["hashes"]["captions_sha256"] == actual

    # -----------------------------------------------------------------------
    # Provenance / lineage fields
    # -----------------------------------------------------------------------

    def test_timing_lock_hash(self, cli_out):
        data = json.loads((cli_out["out_dir"] / "render_output.json").read_text())
        assert data["provenance"]["timing_lock_hash"] == cli_out["plan"].timing_lock_hash

    def test_asset_manifest_ref(self, cli_out):
        data = json.loads((cli_out["out_dir"] / "render_output.json").read_text())
        assert data["asset_manifest_ref"].startswith("file://")

    def test_renderer_field(self, cli_out):
        data = json.loads((cli_out["out_dir"] / "render_output.json").read_text())
        assert data["provenance"]["renderer"] == "video"


@pytest.mark.slow
class TestVerifyCli:

    @pytest.fixture(autouse=True)
    def _need_ffmpeg(self, require_ffmpeg): ...

    @pytest.fixture(scope="class")
    def verify_out(self, tmp_path_factory, sample_manifest, sample_plan):
        run_dir = tmp_path_factory.mktemp("verify_cli_run")
        manifest_path = run_dir / "AssetManifest.json"
        plan_path = run_dir / "RenderPlan.json"
        manifest_path.write_text(sample_manifest.model_dump_json(), encoding="utf-8")
        plan_path.write_text(sample_plan.model_dump_json(), encoding="utf-8")
        out_dir = tmp_path_factory.mktemp("verify_cli_out")
        result = subprocess.run(
            [sys.executable, str(SMOKE_SCRIPT),
             "--asset-manifest", str(manifest_path),
             "--render-plan",    str(plan_path),
             "--out-dir",        str(out_dir),
             "--verify"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, (
            f"--verify exited {result.returncode}:\n{result.stdout}\n{result.stderr}"
        )
        return {"out_dir": out_dir, "stdout": result.stdout}

    def test_fingerprint_json_exists(self, verify_out):
        assert (verify_out["out_dir"] / "render_fingerprint.json").exists()

    def test_mp4_and_srt_exist(self, verify_out):
        assert (verify_out["out_dir"] / "output.mp4").exists()
        assert (verify_out["out_dir"] / "output.srt").exists()

    def test_stdout_is_fingerprint_json(self, verify_out):
        import json as _json
        data = _json.loads(verify_out["stdout"])
        assert "inputs_digest" in data
        assert "mp4_sha256" in data
        assert "srt_sha256" in data
        assert "frame_hashes" in data

    def test_no_timestamps_in_stdout(self, verify_out):
        import json as _json
        data = _json.loads(verify_out["stdout"])
        for bad in ("rendered_at", "timestamp", "created_at"):
            assert bad not in data

    def test_verify_dry_run_mutually_exclusive(self, tmp_path):
        result = subprocess.run(
            [sys.executable, str(SMOKE_SCRIPT),
             "--asset-manifest", str(tmp_path / "m.json"),
             "--render-plan",    str(tmp_path / "p.json"),
             "--out-dir",        str(tmp_path / "out"),
             "--verify", "--dry-run"],
            capture_output=True, text=True,
        )
        assert result.returncode == 1
        assert "mutually exclusive" in result.stderr
