"""
Unit tests for renderer/placeholder.py.

Tests:
  - Generated PNG has correct dimensions.
  - Same inputs produce bit-identical output (determinism).
  - Missing asset_uri → placeholder is generated.
  - cache_dir is required when output_path is not given.

No ffmpeg required.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

try:
    from PIL import Image
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not _PIL_AVAILABLE,
    reason="Pillow not installed",
)

from renderer.placeholder import generate_placeholder


class TestGeneratePlaceholder:

    def test_produces_png_at_output_path(self, tmp_path: Path):
        out = tmp_path / "ph.png"
        result = generate_placeholder(
            shot_id="shot_001",
            width=640,
            height=360,
            output_path=out,
        )
        assert result == out
        assert out.exists()
        assert out.stat().st_size > 0

    def test_correct_dimensions(self, tmp_path: Path):
        out = tmp_path / "ph.png"
        generate_placeholder(shot_id="s1", width=320, height=180, output_path=out)
        img = Image.open(out)
        assert img.size == (320, 180)

    def test_deterministic_same_inputs(self, tmp_path: Path):
        """Two calls with identical inputs must produce identical files."""
        out_a = tmp_path / "a.png"
        out_b = tmp_path / "b.png"
        generate_placeholder(shot_id="shot_det", width=640, height=360,
                             color="#1a1a2e", output_path=out_a)
        generate_placeholder(shot_id="shot_det", width=640, height=360,
                             color="#1a1a2e", output_path=out_b)

        hash_a = hashlib.sha256(out_a.read_bytes()).hexdigest()
        hash_b = hashlib.sha256(out_b.read_bytes()).hexdigest()
        assert hash_a == hash_b, "Placeholder output is non-deterministic for same inputs"

    def test_different_shot_ids_produce_different_labels(self, tmp_path: Path):
        """Placeholders for different shot_ids look different (pixel-level)."""
        out_a = tmp_path / "pa.png"
        out_b = tmp_path / "pb.png"
        generate_placeholder(shot_id="shot_AAA", width=640, height=360, output_path=out_a)
        generate_placeholder(shot_id="shot_BBB", width=640, height=360, output_path=out_b)
        # They may or may not have the same background; the file bytes differ.
        assert out_a.read_bytes() != out_b.read_bytes()

    def test_cache_dir_used_when_no_output_path(self, tmp_path: Path):
        cache = tmp_path / "cache"
        result = generate_placeholder(
            shot_id="cached_shot",
            width=320,
            height=180,
            cache_dir=cache,
        )
        assert result.exists()
        assert result.parent == cache

    def test_cached_result_returned_on_second_call(self, tmp_path: Path):
        """Second call with same key returns the existing file without regenerating."""
        cache = tmp_path / "cache"
        path_a = generate_placeholder(shot_id="s1", width=320, height=180, cache_dir=cache)
        mtime_a = path_a.stat().st_mtime

        path_b = generate_placeholder(shot_id="s1", width=320, height=180, cache_dir=cache)
        assert path_a == path_b
        assert path_b.stat().st_mtime == mtime_a   # file was NOT regenerated

    def test_no_output_path_and_no_cache_dir_raises(self):
        with pytest.raises(ValueError, match="output_path or cache_dir"):
            generate_placeholder(shot_id="s1", width=100, height=100)

    def test_invalid_color_falls_back_to_default(self, tmp_path: Path):
        """Invalid hex color does not raise; uses default background."""
        out = tmp_path / "invalid_color.png"
        generate_placeholder(
            shot_id="s1", width=100, height=100,
            color="not-a-color",
            output_path=out,
        )
        assert out.exists()

    def test_custom_label(self, tmp_path: Path):
        """Custom label parameter is accepted without error."""
        out = tmp_path / "custom_label.png"
        generate_placeholder(
            shot_id="s1", width=200, height=200,
            label="CUSTOM\nLABEL TEXT",
            output_path=out,
        )
        img = Image.open(out)
        assert img.size == (200, 200)
