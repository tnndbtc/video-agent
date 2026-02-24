"""
Integration tests for `video render` subcommand (§41.4 canonical interface).

Covers the behaviours that are NOT exercised by test_render_cli_smoke.py
(which only tests the legacy --out-dir adapter):

  - Outputs land at explicit --out / --video / --srt paths
  - --srt defaults to <video-path>.srt when omitted
  - --dry-run writes RenderOutput only; no mp4/srt produced
  - Stdout is valid RenderOutput JSON with correct hash integrity
  - Missing required flags exit non-zero

Marked @pytest.mark.slow (requires ffmpeg).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

VIDEO_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "video.py"


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
# Shared fixture: write native-format manifest + plan to disk
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def render_fixture_files(tmp_path_factory):
    """Write the minimal verify fixture (native Pydantic format) to disk."""
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        pytest.skip("Pillow not installed")

    sys.path.insert(0, str(VIDEO_SCRIPT.parents[1] / "tools"))
    from tests._fixture_builders import build_minimal_verify_fixture

    manifest, plan = build_minimal_verify_fixture()
    d = tmp_path_factory.mktemp("render_cli_fixtures")
    manifest_path = d / "AssetManifest.json"
    plan_path = d / "RenderPlan.json"
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    plan_path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    return manifest_path, plan_path


# ---------------------------------------------------------------------------
# Standard render — outputs at explicit separate paths
# ---------------------------------------------------------------------------

@pytest.mark.slow
class TestVideoRenderExplicitPaths:
    """video render with --out / --video / --srt as distinct explicit paths."""

    @pytest.fixture(autouse=True)
    def _need_ffmpeg(self, require_ffmpeg): ...

    @pytest.fixture(scope="class")
    def render_out(self, tmp_path_factory, render_fixture_files):
        manifest_path, plan_path = render_fixture_files
        out_dir = tmp_path_factory.mktemp("render_explicit_out")
        result = subprocess.run(
            [
                sys.executable, str(VIDEO_SCRIPT), "render",
                "--manifest", str(manifest_path),
                "--plan",     str(plan_path),
                "--out",      str(out_dir / "RenderOutput.json"),
                "--video",    str(out_dir / "output.mp4"),
                "--srt",      str(out_dir / "output.srt"),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"video render exited {result.returncode}:\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
        return {"out_dir": out_dir, "stdout": result.stdout}

    def test_render_output_json_at_out_path(self, render_out):
        assert (render_out["out_dir"] / "RenderOutput.json").exists()

    def test_mp4_at_video_path(self, render_out):
        assert (render_out["out_dir"] / "output.mp4").exists()

    def test_srt_at_srt_path(self, render_out):
        assert (render_out["out_dir"] / "output.srt").exists()

    def test_stdout_is_valid_render_output_json(self, render_out):
        data = json.loads(render_out["stdout"])
        assert data["schema_version"] == "0.0.1"
        assert "output_id" in data
        assert "hashes" in data

    def test_video_sha256_matches_file(self, render_out):
        data = json.loads((render_out["out_dir"] / "RenderOutput.json").read_text())
        actual = _sha256_file(render_out["out_dir"] / "output.mp4")
        assert data["hashes"]["video_sha256"] == actual

    def test_captions_sha256_matches_file(self, render_out):
        data = json.loads((render_out["out_dir"] / "RenderOutput.json").read_text())
        actual = _sha256_text(
            (render_out["out_dir"] / "output.srt").read_text(encoding="utf-8")
        )
        assert data["hashes"]["captions_sha256"] == actual

    def test_render_output_written_to_out_not_out_dir(self, render_out):
        """RenderOutput.json must be at --out, not at a default location."""
        assert (render_out["out_dir"] / "RenderOutput.json").exists()
        # Ensure no stray render_output.json landed elsewhere
        assert not (render_out["out_dir"] / "render_output.json").exists()


# ---------------------------------------------------------------------------
# --srt defaults to <video>.srt when omitted
# ---------------------------------------------------------------------------

@pytest.mark.slow
class TestVideoRenderSrtDefault:
    """--srt omitted → srt lands at video_path.with_suffix('.srt')."""

    @pytest.fixture(autouse=True)
    def _need_ffmpeg(self, require_ffmpeg): ...

    @pytest.fixture(scope="class")
    def render_out(self, tmp_path_factory, render_fixture_files):
        manifest_path, plan_path = render_fixture_files
        out_dir = tmp_path_factory.mktemp("render_srt_default")
        video_path = out_dir / "myvideo.mp4"
        result = subprocess.run(
            [
                sys.executable, str(VIDEO_SCRIPT), "render",
                "--manifest", str(manifest_path),
                "--plan",     str(plan_path),
                "--out",      str(out_dir / "RenderOutput.json"),
                "--video",    str(video_path),
                # --srt intentionally omitted
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"video render exited {result.returncode}:\n{result.stderr}"
        )
        return {"out_dir": out_dir, "video_path": video_path}

    def test_srt_next_to_video(self, render_out):
        expected_srt = render_out["video_path"].with_suffix(".srt")
        assert expected_srt.exists(), f"Expected SRT at {expected_srt}"


# ---------------------------------------------------------------------------
# --dry-run: RenderOutput written, no mp4/srt
# ---------------------------------------------------------------------------

@pytest.mark.slow
class TestVideoRenderDryRun:
    """--dry-run writes RenderOutput.json but must not produce mp4 or srt."""

    @pytest.fixture(autouse=True)
    def _need_ffmpeg(self, require_ffmpeg): ...

    @pytest.fixture(scope="class")
    def dry_run_out(self, tmp_path_factory, render_fixture_files):
        manifest_path, plan_path = render_fixture_files
        out_dir = tmp_path_factory.mktemp("render_dry_run")
        result = subprocess.run(
            [
                sys.executable, str(VIDEO_SCRIPT), "render",
                "--manifest", str(manifest_path),
                "--plan",     str(plan_path),
                "--out",      str(out_dir / "RenderOutput.json"),
                "--video",    str(out_dir / "output.mp4"),
                "--dry-run",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"video render --dry-run exited {result.returncode}:\n{result.stderr}"
        )
        return {"out_dir": out_dir, "stdout": result.stdout}

    def test_render_output_json_written(self, dry_run_out):
        assert (dry_run_out["out_dir"] / "RenderOutput.json").exists()

    def test_mp4_not_produced(self, dry_run_out):
        assert not (dry_run_out["out_dir"] / "output.mp4").exists()

    def test_srt_not_produced(self, dry_run_out):
        # Default srt path (output.srt) must also be absent
        assert not (dry_run_out["out_dir"] / "output.srt").exists()

    def test_stdout_is_valid_json(self, dry_run_out):
        data = json.loads(dry_run_out["stdout"])
        assert "schema_version" in data
        assert "effective_settings" in data


# ---------------------------------------------------------------------------
# Argument validation: missing required flags exit non-zero
# ---------------------------------------------------------------------------

@pytest.mark.slow
class TestVideoRenderArgValidation:

    def test_missing_manifest_exits_nonzero(self, tmp_path):
        result = subprocess.run(
            [
                sys.executable, str(VIDEO_SCRIPT), "render",
                "--plan",  str(tmp_path / "plan.json"),
                "--out",   str(tmp_path / "out.json"),
                "--video", str(tmp_path / "out.mp4"),
            ],
            capture_output=True, text=True,
        )
        assert result.returncode != 0

    def test_missing_plan_exits_nonzero(self, tmp_path):
        result = subprocess.run(
            [
                sys.executable, str(VIDEO_SCRIPT), "render",
                "--manifest", str(tmp_path / "manifest.json"),
                "--out",      str(tmp_path / "out.json"),
                "--video",    str(tmp_path / "out.mp4"),
            ],
            capture_output=True, text=True,
        )
        assert result.returncode != 0

    def test_missing_out_exits_nonzero(self, tmp_path):
        result = subprocess.run(
            [
                sys.executable, str(VIDEO_SCRIPT), "render",
                "--manifest", str(tmp_path / "manifest.json"),
                "--plan",     str(tmp_path / "plan.json"),
                "--video",    str(tmp_path / "out.mp4"),
            ],
            capture_output=True, text=True,
        )
        assert result.returncode != 0

    def test_missing_video_exits_nonzero(self, tmp_path):
        result = subprocess.run(
            [
                sys.executable, str(VIDEO_SCRIPT), "render",
                "--manifest", str(tmp_path / "manifest.json"),
                "--plan",     str(tmp_path / "plan.json"),
                "--out",      str(tmp_path / "out.json"),
            ],
            capture_output=True, text=True,
        )
        assert result.returncode != 0

    def test_nonexistent_manifest_exits_nonzero(self, tmp_path):
        result = subprocess.run(
            [
                sys.executable, str(VIDEO_SCRIPT), "render",
                "--manifest", str(tmp_path / "no_such.json"),
                "--plan",     str(tmp_path / "no_such_plan.json"),
                "--out",      str(tmp_path / "out.json"),
                "--video",    str(tmp_path / "out.mp4"),
            ],
            capture_output=True, text=True,
        )
        assert result.returncode != 0
