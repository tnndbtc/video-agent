"""Unit tests for PreviewRenderer dry-run mode."""
from __future__ import annotations
import pytest
from pathlib import Path

TOOLS_ROOT = Path(__file__).resolve().parents[2]
import sys; sys.path.insert(0, str(TOOLS_ROOT))

from schemas.asset_manifest import AssetManifest, Shot, VisualAsset
from schemas.render_plan import FallbackConfig, RenderPlan, Resolution
from renderer.preview_local import PreviewRenderer

_TIMING = "sha256:test-timing-lock-abc123"


def _make_manifest() -> AssetManifest:
    return AssetManifest(
        manifest_id="dry-m",
        project_id="dry-p",
        shotlist_ref="file:///sl.json",
        timing_lock_hash=_TIMING,
        shots=[Shot(shot_id="s1", duration_ms=2_000)],
    )


def _make_plan() -> RenderPlan:
    return RenderPlan(
        plan_id="dry-pl",
        project_id="dry-p",
        profile="preview_local",
        resolution=Resolution(width=1280, height=720, aspect="16:9"),
        fps=24,
        asset_manifest_ref="file:///render_plan.json",   # becomes render_plan_ref
        timing_lock_hash=_TIMING,
        fallback=FallbackConfig(),
    )


class TestDryRun:

    _ASSET_MANIFEST_REF = "file:///asset_manifest.json"

    @pytest.fixture()
    def dry_result(self, tmp_path):
        return PreviewRenderer(
            _make_manifest(),
            _make_plan(),
            output_dir=tmp_path / "out",
            asset_manifest_ref=self._ASSET_MANIFEST_REF,
            dry_run=True,
        ).render()

    def test_render_plan_ref(self, dry_result):
        assert dry_result.render_plan_ref == "file:///render_plan.json"

    def test_asset_manifest_ref(self, dry_result):
        assert dry_result.asset_manifest_ref == self._ASSET_MANIFEST_REF

    def test_video_uri_is_none(self, dry_result):
        assert dry_result.video_uri is None

    def test_outputs_empty(self, dry_result):
        assert dry_result.outputs == []

    def test_effective_settings_present(self, dry_result):
        assert dry_result.effective_settings is not None
        assert dry_result.effective_settings.encoder == "libx264"

    def test_only_json_written(self, tmp_path):
        out = tmp_path / "out2"
        PreviewRenderer(
            _make_manifest(), _make_plan(),
            output_dir=out,
            asset_manifest_ref=self._ASSET_MANIFEST_REF,
            dry_run=True,
        ).render()
        assert {f.name for f in out.iterdir()} == {"render_output.json"}

    def test_inputs_digest_is_hex(self, dry_result):
        assert len(dry_result.inputs_digest) == 64
        assert all(c in "0123456789abcdef" for c in dry_result.inputs_digest)

    def test_inputs_digest_pinned(self, dry_result):
        assert dry_result.inputs_digest == "86b7f38776520babf632ef58b7b2cb7c4e2ffa703ce9d8b8f57102b68c096ab1"

    def test_inputs_digest_determinism(self, tmp_path):
        import json as _json

        def _get_digest(out):
            PreviewRenderer(
                _make_manifest(), _make_plan(),
                output_dir=out,
                asset_manifest_ref=self._ASSET_MANIFEST_REF,
                dry_run=True,
            ).render()
            data = _json.loads((out / "render_output.json").read_bytes())
            return data["inputs_digest"]

        assert _get_digest(tmp_path / "a") == _get_digest(tmp_path / "b")

    def test_schema_metadata(self, dry_result):
        assert dry_result.schema_id == "RenderOutput"
        assert dry_result.schema_version == "0.0.1"
        assert dry_result.producer.name == "PreviewRenderer"

    def test_dry_run_json_bytes_deterministic(self, tmp_path):
        """rendered_at is now 'dry-run' so full JSON bytes must be identical."""
        def _bytes(out):
            PreviewRenderer(
                _make_manifest(), _make_plan(),
                output_dir=out,
                asset_manifest_ref=self._ASSET_MANIFEST_REF,
                dry_run=True,
            ).render()
            return (out / "render_output.json").read_bytes()

        assert _bytes(tmp_path / "a") == _bytes(tmp_path / "b")


@pytest.mark.slow
class TestNonRegression:
    """Verify Wave-2 does not change mp4/srt bytes for identical inputs."""

    @pytest.fixture(scope="class")
    def minimal_render(self, tmp_path_factory, require_ffmpeg):
        try:
            from PIL import Image
        except ImportError:
            pytest.skip("Pillow not installed")

        assets = tmp_path_factory.mktemp("assets")
        out    = tmp_path_factory.mktemp("out")
        # 1-shot, 500 ms, solid red PNG
        img = Image.new("RGB", (1280, 720), color=(200, 60, 60))
        png = assets / "s1.png"
        img.save(png, compress_level=9, optimize=False)
        manifest = AssetManifest(
            manifest_id="nr-m", project_id="nr-p",
            shotlist_ref="file:///sl.json",
            timing_lock_hash=_TIMING,
            shots=[Shot(
                shot_id="s1", duration_ms=500,
                visual_assets=[VisualAsset(asset_id="a1", asset_uri=png.as_uri())],
            )],
        )
        plan = RenderPlan(
            plan_id="nr-pl", project_id="nr-p",
            profile="preview_local",
            resolution=Resolution(width=1280, height=720, aspect="16:9"),
            fps=24,
            asset_manifest_ref="file:///render_plan.json",
            timing_lock_hash=_TIMING,
            fallback=FallbackConfig(),
            asset_resolutions={"a1": png.as_uri()},
        )
        result = PreviewRenderer(
            manifest, plan, output_dir=out,
            asset_manifest_ref="file:///asset_manifest.json",
        ).render()
        return result, out

    def test_mp4_sha256_unchanged(self, minimal_render):
        result, out = minimal_render
        assert result.hashes.video_sha256 == "d5524d393dd582a8f6e608390080f4c805c00c93a40ae39fe26466a1b2d7c6aa"

    def test_srt_sha256_unchanged(self, minimal_render):
        result, out = minimal_render
        # No VO lines → SRT is written empty; captions_sha256 is SHA-256 of ""
        assert result.hashes.captions_sha256 == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    def test_inputs_digest_present_in_full_render(self, minimal_render):
        result, _ = minimal_render
        assert len(result.inputs_digest) == 64
        assert result.schema_id == "RenderOutput"
        assert result.schema_version == "0.0.1"


class TestFromFiles:

    def test_missing_manifest_raises(self, tmp_path):
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(_make_plan().model_dump_json())
        with pytest.raises(FileNotFoundError, match="ERROR: missing required input: manifest.json"):
            PreviewRenderer.from_files(
                manifest_path=tmp_path / "manifest.json",
                plan_path=plan_file,
                output_dir=tmp_path / "out",
            )

    def test_missing_plan_raises(self, tmp_path):
        manifest_file = tmp_path / "manifest.json"
        manifest_file.write_text(_make_manifest().model_dump_json())
        with pytest.raises(FileNotFoundError, match="ERROR: missing required input: plan.json"):
            PreviewRenderer.from_files(
                manifest_path=manifest_file,
                plan_path=tmp_path / "plan.json",
                output_dir=tmp_path / "out",
            )

    def test_valid_files_produces_renderer(self, tmp_path):
        manifest_file = tmp_path / "manifest.json"
        plan_file = tmp_path / "plan.json"
        manifest_file.write_text(_make_manifest().model_dump_json())
        plan_file.write_text(_make_plan().model_dump_json())
        r = PreviewRenderer.from_files(
            manifest_path=manifest_file,
            plan_path=plan_file,
            output_dir=tmp_path / "out",
            dry_run=True,
        )
        assert isinstance(r, PreviewRenderer)


@pytest.mark.slow
class TestVerifyMode:

    @pytest.fixture(autouse=True)
    def _need_ffmpeg(self, require_ffmpeg): ...

    @pytest.fixture(scope="class")
    def verify_result(self, tmp_path_factory):
        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            pytest.skip("Pillow not installed")
        from tests._fixture_builders import build_minimal_verify_fixture
        manifest, plan = build_minimal_verify_fixture()
        out = tmp_path_factory.mktemp("verify_out")
        r = PreviewRenderer(
            manifest, plan,
            output_dir=out,
            asset_manifest_ref="file:///asset_manifest.json",
            dry_run=False,
        )
        return r.verify(), out

    def test_fingerprint_file_written(self, verify_result):
        _, out = verify_result
        assert (out / "render_fingerprint.json").exists()

    def test_mp4_and_srt_written(self, verify_result):
        _, out = verify_result
        assert (out / "output.mp4").exists()
        assert (out / "output.srt").exists()

    def test_inputs_digest_pinned(self, verify_result):
        fp, _ = verify_result
        # Same manifest+plan as TestDryRun → identical inputs_digest
        assert fp.inputs_digest == "86b7f38776520babf632ef58b7b2cb7c4e2ffa703ce9d8b8f57102b68c096ab1"

    def test_srt_sha256_pinned(self, verify_result):
        fp, _ = verify_result
        # No VO lines → empty SRT → SHA-256("")
        assert fp.srt_sha256 == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    def test_mp4_sha256_pinned(self, verify_result):
        fp, _ = verify_result
        assert fp.mp4_sha256 == "b4a44e354dc6e8808a94a59b7bd402e0496e3d1489223a20a92132a7c8ecd6a9"

    def test_frame_count(self, verify_result):
        fp, _ = verify_result
        # 1 shot × 2000 ms × 24 fps = 48 frames
        assert len(fp.frame_hashes) == 48

    def test_no_timestamp_in_fingerprint_json(self, verify_result):
        _, out = verify_result
        import json as _json
        data = _json.loads((out / "render_fingerprint.json").read_bytes())
        for bad in ("rendered_at", "timestamp", "created_at"):
            assert bad not in data

    def test_fingerprint_json_bytes_deterministic(self, tmp_path):
        """Two verify() calls on identical inputs → byte-identical fingerprint."""
        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            pytest.skip("Pillow not installed")

        def _fp_bytes(out):
            PreviewRenderer(
                _make_manifest(), _make_plan(),
                output_dir=out,
                asset_manifest_ref="file:///asset_manifest.json",
                dry_run=False,
            ).verify()
            return (out / "render_fingerprint.json").read_bytes()

        assert _fp_bytes(tmp_path / "a") == _fp_bytes(tmp_path / "b")


@pytest.mark.slow
class TestHighProfile:
    """Pin tests for profile=high (CRF=18, preset=slow)."""

    @pytest.fixture(autouse=True)
    def _need_ffmpeg(self, require_ffmpeg): ...

    @pytest.fixture(scope="class")
    def high_result(self, tmp_path_factory):
        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            pytest.skip("Pillow not installed")
        from tests._fixture_builders import build_minimal_verify_fixture
        manifest, plan = build_minimal_verify_fixture(profile="high")
        out = tmp_path_factory.mktemp("high_out")
        return PreviewRenderer(
            manifest, plan, output_dir=out,
            asset_manifest_ref="file:///asset_manifest.json",
            dry_run=False,
        ).verify(), out

    def test_inputs_digest_pinned(self, high_result):
        fp, _ = high_result
        assert fp.inputs_digest == "b0baa3766120f32e80d3ef123123697ccd7ef54db3c19e4084363dcf9a1d9846"

    def test_mp4_sha256_pinned(self, high_result):
        fp, _ = high_result
        assert fp.mp4_sha256 == "5e41afd474b4d812d3bcabb226f3effea0f6cdce277eaba48d6d5d2fce0dcaf8"

    def test_srt_sha256_pinned(self, high_result):
        fp, _ = high_result
        assert fp.srt_sha256 == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    def test_fingerprint_json_bytes_deterministic(self, tmp_path):
        """Two verify() calls on high profile → byte-identical fingerprint."""
        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            pytest.skip("Pillow not installed")
        from tests._fixture_builders import build_minimal_verify_fixture

        def _fp_bytes(out):
            manifest, plan = build_minimal_verify_fixture(profile="high")
            PreviewRenderer(
                manifest, plan, output_dir=out,
                asset_manifest_ref="file:///asset_manifest.json",
                dry_run=False,
            ).verify()
            return (out / "render_fingerprint.json").read_bytes()

        assert _fp_bytes(tmp_path / "a") == _fp_bytes(tmp_path / "b")

    def test_mp4_differs_from_preview(self, high_result, tmp_path):
        """High profile mp4 must differ from preview profile mp4."""
        fp_high, _ = high_result
        from tests._fixture_builders import build_minimal_verify_fixture
        manifest, plan = build_minimal_verify_fixture(profile="preview_local")
        fp_preview = PreviewRenderer(
            manifest, plan, output_dir=tmp_path / "prev",
            asset_manifest_ref="file:///asset_manifest.json",
        ).verify()
        assert fp_high.mp4_sha256 != fp_preview.mp4_sha256

    def test_effective_settings_fields(self, tmp_path):
        """Dry-run for high profile must expose crf/preset/profile in effective_settings."""
        from tests._fixture_builders import build_minimal_verify_fixture
        manifest, plan = build_minimal_verify_fixture(profile="high")
        result = PreviewRenderer(
            manifest, plan, output_dir=tmp_path / "out",
            asset_manifest_ref="file:///asset_manifest.json",
            dry_run=True,
        ).render()
        es = result.effective_settings
        assert es.crf == "18"
        assert es.preset == "slow"
        assert es.profile == "high"
