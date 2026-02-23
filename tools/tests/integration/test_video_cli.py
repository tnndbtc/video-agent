"""
Integration tests for scripts/video.py verify.
Requires ffmpeg + Pillow. Marked @pytest.mark.slow.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

VIDEO_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "video.py"


@pytest.mark.slow
class TestVideoVerifyCli:

    @pytest.fixture(autouse=True)
    def _need_ffmpeg(self, require_ffmpeg): ...

    @pytest.fixture(scope="class")
    def verify_run(self):
        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            pytest.skip("Pillow not installed")
        return subprocess.run(
            [sys.executable, str(VIDEO_SCRIPT), "verify"],
            capture_output=True, text=True,
        )

    def test_exit_code_zero(self, verify_run):
        assert verify_run.returncode == 0, (
            f"video verify failed:\nSTDOUT: {verify_run.stdout}\n"
            f"STDERR: {verify_run.stderr}"
        )

    def test_ok_stdout(self, verify_run):
        assert verify_run.stdout.strip() == "OK: video verified"

    def test_stderr_empty_on_success(self, verify_run):
        assert verify_run.stderr == ""

    def test_unknown_command_exits_nonzero(self):
        result = subprocess.run(
            [sys.executable, str(VIDEO_SCRIPT), "bogus"],
            capture_output=True, text=True,
        )
        assert result.returncode != 0


@pytest.mark.slow
class TestVideoVerifyDeterminismFailure:
    """Verify that a corrupted frame hash causes cmd_verify() to fail."""

    @pytest.fixture(autouse=True)
    def _need_ffmpeg(self, require_ffmpeg): ...

    def test_frame_hash_corruption_exits_nonzero(self, monkeypatch, capsys):
        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            pytest.skip("Pillow not installed")

        # Load video module so its globals (e.g. _fingerprint_bytes) are fresh
        spec = importlib.util.spec_from_file_location("video_cli", VIDEO_SCRIPT)
        video_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(video_mod)

        from renderer.preview_local import PreviewRenderer

        original_verify = PreviewRenderer.verify
        call_count = 0

        def patched_verify(self):
            nonlocal call_count
            result = original_verify(self)
            call_count += 1
            if call_count >= 2:
                fp_path = self.output_dir / "render_fingerprint.json"
                data = json.loads(fp_path.read_bytes())
                if data.get("frame_hashes"):
                    data["frame_hashes"][0] = "CORRUPTED"
                    fp_path.write_text(
                        json.dumps(data, indent=2), encoding="utf-8"
                    )
            return result

        monkeypatch.setattr(PreviewRenderer, "verify", patched_verify)

        exit_code = video_mod.cmd_verify()
        captured = capsys.readouterr()

        assert exit_code == 1
        assert captured.out.strip() == "ERROR: video verification failed"
        assert "fingerprint JSON bytes differ" in captured.err


@pytest.mark.slow
class TestVideoVerifyProfile:
    """Test --profile flag for both preview and high profiles."""

    @pytest.fixture(autouse=True)
    def _need_ffmpeg(self, require_ffmpeg): ...

    def test_profile_preview_explicit_exits_zero(self):
        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            pytest.skip("Pillow not installed")
        result = subprocess.run(
            [sys.executable, str(VIDEO_SCRIPT), "verify", "--profile", "preview"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "OK: video verified"

    def test_profile_high_exits_zero(self):
        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            pytest.skip("Pillow not installed")
        result = subprocess.run(
            [sys.executable, str(VIDEO_SCRIPT), "verify", "--profile", "high"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "OK: video verified"

    def test_preview_and_high_fingerprints_differ(self, tmp_path):
        """Preview and high profiles must produce different fingerprint bytes."""
        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            pytest.skip("Pillow not installed")
        import tempfile
        spec = importlib.util.spec_from_file_location("video_cli", VIDEO_SCRIPT)
        video_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(video_mod)
        with (tempfile.TemporaryDirectory() as dp,
              tempfile.TemporaryDirectory() as dh):
            bp = video_mod._fingerprint_bytes(Path(dp), profile="preview")
            bh = video_mod._fingerprint_bytes(Path(dh), profile="high")
        assert bp != bh


@pytest.mark.slow
class TestVideoAuditRenderCli:
    """Tests for `video audit-render` subcommand."""

    @pytest.fixture(autouse=True)
    def _need_ffmpeg(self, require_ffmpeg): ...

    @pytest.fixture(scope="class")
    def fixture_files(self, tmp_path_factory):
        """Write the minimal fixture manifest+plan to disk for CLI consumption."""
        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            pytest.skip("Pillow not installed")
        import sys; sys.path.insert(0, str(VIDEO_SCRIPT.parents[1] / "tools"))
        from tests._fixture_builders import build_minimal_verify_fixture
        manifest, plan = build_minimal_verify_fixture()
        d = tmp_path_factory.mktemp("audit_fixtures")
        manifest_path = d / "asset_manifest.json"
        plan_path     = d / "render_plan.json"
        manifest_path.write_text(manifest.model_dump_json(indent=2))
        plan_path.write_text(plan.model_dump_json(indent=2))
        return manifest_path, plan_path

    def test_audit_exits_zero_on_deterministic_inputs(self, fixture_files):
        manifest_path, plan_path = fixture_files
        result = subprocess.run(
            [sys.executable, str(VIDEO_SCRIPT), "audit-render",
             str(plan_path), str(manifest_path)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr

    def test_audit_stdout_is_valid_json(self, fixture_files):
        manifest_path, plan_path = fixture_files
        result = subprocess.run(
            [sys.executable, str(VIDEO_SCRIPT), "audit-render",
             str(plan_path), str(manifest_path)],
            capture_output=True, text=True,
        )
        data = json.loads(result.stdout)
        assert data["status"] == "pass"
        assert data["diff_fields"] == []

    def test_audit_dry_run_exits_zero(self, fixture_files):
        manifest_path, plan_path = fixture_files
        result = subprocess.run(
            [sys.executable, str(VIDEO_SCRIPT), "audit-render",
             str(plan_path), str(manifest_path), "--dry-run"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr

    def test_audit_dry_run_status_pass(self, fixture_files):
        manifest_path, plan_path = fixture_files
        result = subprocess.run(
            [sys.executable, str(VIDEO_SCRIPT), "audit-render",
             str(plan_path), str(manifest_path), "--dry-run"],
            capture_output=True, text=True,
        )
        data = json.loads(result.stdout)
        assert data["status"] == "pass"

    def test_audit_missing_plan_exits_nonzero(self, tmp_path):
        result = subprocess.run(
            [sys.executable, str(VIDEO_SCRIPT), "audit-render",
             str(tmp_path / "missing.json"), str(tmp_path / "m.json")],
            capture_output=True, text=True,
        )
        assert result.returncode != 0


@pytest.mark.slow
class TestVideoVerifyHashPins:
    """
    T1: default render → mp4_sha256 == pinned_preview
    T2: --profile preview → mp4_sha256 == pinned_preview
    T3: --profile high → mp4_sha256 == pinned_high
    + prove default invocation is identical to explicit --profile preview
    """

    _PINNED_PREVIEW = "b4a44e354dc6e8808a94a59b7bd402e0496e3d1489223a20a92132a7c8ecd6a9"
    _PINNED_HIGH    = "5e41afd474b4d812d3bcabb226f3effea0f6cdce277eaba48d6d5d2fce0dcaf8"

    @pytest.fixture(autouse=True)
    def _need_ffmpeg(self, require_ffmpeg): ...

    @pytest.fixture(scope="class")
    def video_mod(self):
        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            pytest.skip("Pillow not installed")
        spec = importlib.util.spec_from_file_location("video_cli", VIDEO_SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    @pytest.fixture(scope="class")
    def default_fp(self, video_mod, tmp_path_factory):
        d = tmp_path_factory.mktemp("default_fp")
        return json.loads(video_mod._fingerprint_bytes(d))   # no profile → default

    @pytest.fixture(scope="class")
    def preview_fp(self, video_mod, tmp_path_factory):
        d = tmp_path_factory.mktemp("preview_fp")
        return json.loads(video_mod._fingerprint_bytes(d, profile="preview"))

    @pytest.fixture(scope="class")
    def high_fp(self, video_mod, tmp_path_factory):
        d = tmp_path_factory.mktemp("high_fp")
        return json.loads(video_mod._fingerprint_bytes(d, profile="high"))

    def test_default_mp4_sha256_matches_pinned(self, default_fp):
        """T1: default invocation → pinned preview hash."""
        assert default_fp["mp4_sha256"] == self._PINNED_PREVIEW

    def test_preview_mp4_sha256_matches_pinned(self, preview_fp):
        """T2: explicit --profile preview → same pinned hash."""
        assert preview_fp["mp4_sha256"] == self._PINNED_PREVIEW

    def test_high_mp4_sha256_matches_pinned(self, high_fp):
        """T3: --profile high → pinned high hash."""
        assert high_fp["mp4_sha256"] == self._PINNED_HIGH

    def test_default_equals_preview(self, default_fp, preview_fp):
        """default == --profile preview (hash identity)."""
        assert default_fp["mp4_sha256"] == preview_fp["mp4_sha256"]


@pytest.mark.slow
class TestVideoAuditRenderHighProfile:
    """T4 for high profile: audit-render proves RenderOutput.json + fingerprint are stable."""

    @pytest.fixture(autouse=True)
    def _need_ffmpeg(self, require_ffmpeg): ...

    @pytest.fixture(scope="class")
    def high_fixture_files(self, tmp_path_factory):
        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            pytest.skip("Pillow not installed")
        import sys; sys.path.insert(0, str(VIDEO_SCRIPT.parents[1] / "tools"))
        from tests._fixture_builders import build_minimal_verify_fixture
        manifest, plan = build_minimal_verify_fixture(profile="high")
        d = tmp_path_factory.mktemp("audit_high")
        manifest_path = d / "asset_manifest.json"
        plan_path     = d / "render_plan.json"
        manifest_path.write_text(manifest.model_dump_json(indent=2))
        plan_path.write_text(plan.model_dump_json(indent=2))
        return manifest_path, plan_path

    def test_audit_high_exits_zero(self, high_fixture_files):
        manifest_path, plan_path = high_fixture_files
        result = subprocess.run(
            [sys.executable, str(VIDEO_SCRIPT), "audit-render",
             str(plan_path), str(manifest_path)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr

    def test_audit_high_status_pass(self, high_fixture_files):
        manifest_path, plan_path = high_fixture_files
        result = subprocess.run(
            [sys.executable, str(VIDEO_SCRIPT), "audit-render",
             str(plan_path), str(manifest_path)],
            capture_output=True, text=True,
        )
        data = json.loads(result.stdout)
        assert data["status"] == "pass"
        assert data["diff_fields"] == []

    def test_audit_high_dry_run_exits_zero(self, high_fixture_files):
        manifest_path, plan_path = high_fixture_files
        result = subprocess.run(
            [sys.executable, str(VIDEO_SCRIPT), "audit-render",
             str(plan_path), str(manifest_path), "--dry-run"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr

    def test_audit_high_dry_run_status_pass(self, high_fixture_files):
        manifest_path, plan_path = high_fixture_files
        result = subprocess.run(
            [sys.executable, str(VIDEO_SCRIPT), "audit-render",
             str(plan_path), str(manifest_path), "--dry-run"],
            capture_output=True, text=True,
        )
        data = json.loads(result.stdout)
        assert data["status"] == "pass"
