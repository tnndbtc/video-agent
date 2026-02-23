#!/usr/bin/env python3
"""
Generate Test Media for Integration Tests

Creates minimal test media files for render pipeline integration tests.
Uses ffmpeg to generate:
- Colored test images (JPG)
- Test pattern videos (MP4)
- Sine wave audio (MP3)

Usage:
    python scripts/generate_test_media.py /path/to/test_assets

    # Or with environment variable
    export VIDEO_TEST_ASSETS=/path/to/test_assets
    python scripts/generate_test_media.py

Requirements:
    - ffmpeg must be installed and in PATH
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path


def check_ffmpeg():
    """Verify ffmpeg is installed and accessible."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True,
            check=True
        )
        print("✅ ffmpeg found:", result.stdout.split('\n')[0])
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("❌ ffmpeg not found. Please install ffmpeg first.")
        return False


def create_test_image(output_path: Path, color: str, width: int = 1920, height: int = 1080):
    """Create a solid color test image using ffmpeg.

    Args:
        output_path: Path to output JPG file
        color: Color name (blue, red, green, etc.)
        width: Image width in pixels
        height: Image height in pixels
    """
    if output_path.exists():
        print(f"  Skipping {output_path.name} (already exists)")
        return
    print(f"  Creating {output_path.name} ({color})...")

    cmd = [
        "ffmpeg",
        "-f", "lavfi",
        "-i", f"color=c={color}:s={width}x{height}:d=1",
        "-frames:v", "1",
        "-y",  # Overwrite if exists
        str(output_path)
    ]

    subprocess.run(cmd, capture_output=True, check=True)
    print(f"    ✅ Created {output_path.name} ({output_path.stat().st_size} bytes)")


def create_test_video(output_path: Path, duration: int = 5, width: int = 1920, height: int = 1080, fps: int = 30):
    """Create a test pattern video using ffmpeg.

    Args:
        output_path: Path to output MP4 file
        duration: Video duration in seconds
        width: Video width in pixels
        height: Video height in pixels
        fps: Frames per second
    """
    if output_path.exists():
        print(f"  Skipping {output_path.name} (already exists)")
        return
    print(f"  Creating {output_path.name} ({duration}s test pattern)...")

    cmd = [
        "ffmpeg",
        "-f", "lavfi",
        "-i", f"testsrc=duration={duration}:size={width}x{height}:rate={fps}",
        "-pix_fmt", "yuv420p",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-y",  # Overwrite if exists
        str(output_path)
    ]

    subprocess.run(cmd, capture_output=True, check=True)
    print(f"    ✅ Created {output_path.name} ({output_path.stat().st_size} bytes)")


def create_test_audio(output_path: Path, duration: int = 60, frequency: int = 1000):
    """Create a sine wave audio file using ffmpeg.

    Args:
        output_path: Path to output MP3 file
        duration: Audio duration in seconds
        frequency: Sine wave frequency in Hz
    """
    if output_path.exists():
        print(f"  Skipping {output_path.name} (already exists)")
        return
    print(f"  Creating {output_path.name} ({duration}s sine wave)...")

    cmd = [
        "ffmpeg",
        "-f", "lavfi",
        "-i", f"sine=frequency={frequency}:duration={duration}",
        "-ac", "2",  # Stereo
        "-ar", "44100",  # Sample rate
        "-b:a", "192k",  # Bitrate
        "-y",  # Overwrite if exists
        str(output_path)
    ]

    subprocess.run(cmd, capture_output=True, check=True)
    print(f"    ✅ Created {output_path.name} ({output_path.stat().st_size} bytes)")


def generate_test_assets(base_path: Path):
    """Generate all test media assets.

    Args:
        base_path: Root directory for test assets
    """
    print(f"\n🎬 Generating test media in: {base_path}\n")

    # Create directory structure
    images_dir = base_path / "images"
    videos_dir = base_path / "videos"
    audio_dir = base_path / "audio"

    images_dir.mkdir(parents=True, exist_ok=True)
    videos_dir.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)

    print("📁 Created directory structure\n")

    # Generate images
    print("🖼️  Generating test images...")
    create_test_image(images_dir / "test1.jpg", "blue")
    create_test_image(images_dir / "test2.jpg", "red")
    create_test_image(images_dir / "test3.jpg", "green")
    print()

    # Generate videos
    print("🎥 Generating test videos...")
    create_test_video(videos_dir / "test1.mp4", duration=5)
    create_test_video(videos_dir / "test2.mp4", duration=5)
    print()

    # Generate audio
    print("🔊 Generating test audio...")
    create_test_audio(audio_dir / "audio.mp3", duration=60)
    print()

    print("✅ All test media generated successfully!\n")

    # Print summary
    print("📊 Summary:")
    print(f"   Images: {len(list(images_dir.glob('*.jpg')))} files")
    print(f"   Videos: {len(list(videos_dir.glob('*.mp4')))} files")
    print(f"   Audio:  {len(list(audio_dir.glob('*.mp3')))} files")
    print()

    print("🚀 Ready to run integration tests:")
    print(f"   export VIDEO_TEST_ASSETS={base_path}")
    print("   pytest worker/tests/integration/ -v")
    print()


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Generate test media for render pipeline integration tests",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Generate in specific directory
    python scripts/generate_test_media.py /tmp/test_assets

    # Use environment variable
    export VIDEO_TEST_ASSETS=/tmp/test_assets
    python scripts/generate_test_media.py

    # Generate and run tests
    python scripts/generate_test_media.py /tmp/test_assets
    export VIDEO_TEST_ASSETS=/tmp/test_assets
    pytest worker/tests/integration/ -v
        """
    )

    parser.add_argument(
        "output_dir",
        nargs="?",
        help="Output directory for test assets (default: $VIDEO_TEST_ASSETS)"
    )

    parser.add_argument(
        "--out",
        dest="output_dir_flag",
        help="Output directory for test assets (alternative to positional arg)",
    )

    args = parser.parse_args()

    # Determine output directory — priority: positional arg → --out → $VIDEO_TEST_ASSETS → error
    if args.output_dir:
        output_dir = Path(args.output_dir)
    elif args.output_dir_flag:
        output_dir = Path(args.output_dir_flag)
    elif "VIDEO_TEST_ASSETS" in os.environ:
        output_dir = Path(os.environ["VIDEO_TEST_ASSETS"])
    else:
        print("❌ Error: No output directory specified")
        print()
        print("Please provide output directory:")
        print("  python scripts/generate_test_media.py /path/to/test_assets")
        print("  python scripts/generate_test_media.py --out /path/to/test_assets")
        print()
        print("Or set VIDEO_TEST_ASSETS environment variable:")
        print("  export VIDEO_TEST_ASSETS=/path/to/test_assets")
        print("  python scripts/generate_test_media.py")
        sys.exit(1)

    # Check ffmpeg availability
    if not check_ffmpeg():
        sys.exit(1)

    # Generate assets
    try:
        generate_test_assets(output_dir)
    except subprocess.CalledProcessError as e:
        print(f"\n❌ Error generating test media: {e}")
        if e.stderr:
            print(f"   ffmpeg error: {e.stderr.decode()}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
