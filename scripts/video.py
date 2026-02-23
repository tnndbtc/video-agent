#!/usr/bin/env python3
"""
video — top-level renderer CLI.

Usage:
    python scripts/video.py verify
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

_TOOLS_ROOT = Path(__file__).resolve().parents[1] / "tools"
if str(_TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(_TOOLS_ROOT))

from tests._fixture_builders import build_minimal_verify_fixture
from renderer.preview_local import PreviewRenderer
from schemas.render_output import RenderAudit

_SKIP_RENDER_OUTPUT_FIELDS = frozenset({
    "rendered_at",      # wall-clock timestamp — intentionally non-deterministic
    "video_uri",        # absolute temp path — differs per tempdir
    "captions_uri",     # absolute temp path
    "audio_stems_uri",  # absolute temp path (always None in Phase 0)
    "outputs",          # list contains absolute paths
})

_PINNED_SRT_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
_PINNED_MP4_SHA256: dict[str, str] = {
    "preview": "b4a44e354dc6e8808a94a59b7bd402e0496e3d1489223a20a92132a7c8ecd6a9",
    "high": "5e41afd474b4d812d3bcabb226f3effea0f6cdce277eaba48d6d5d2fce0dcaf8",
}

_CLI_TO_PLAN_PROFILE: dict[str, str] = {
    "preview": "preview_local",   # keeps byte-identical default behavior
    "high": "high",
}


def _diff_json(
    a: dict,
    b: dict,
    *,
    skip: frozenset[str] = frozenset(),
    prefix: str = "",
) -> list[str]:
    """Recursively diff two JSON dicts; return sorted list of differing field paths."""
    diffs: list[str] = []
    for key in sorted(set(a) | set(b)):
        path = f"{prefix}.{key}" if prefix else key
        if key in skip:
            continue
        va, vb = a.get(key), b.get(key)
        if isinstance(va, dict) and isinstance(vb, dict):
            diffs.extend(_diff_json(va, vb, skip=skip, prefix=path))
        elif isinstance(va, list) and isinstance(vb, list):
            for i, (ea, eb) in enumerate(zip(va, vb)):
                if ea != eb:
                    diffs.append(f"{path}[{i}]")
            if len(va) != len(vb):
                diffs.append(f"{path}[length_mismatch]")
        elif va != vb:
            diffs.append(path)
    return diffs


def cmd_audit_render(
    render_plan: str,
    asset_manifest: str,
    dry_run: bool = False,
) -> int:
    """
    Render <render_plan> + <asset_manifest> twice; compare outputs for nondeterminism.
    Writes RenderAudit JSON to stdout. Returns 0 if pass, 1 if fail or error.
    """
    import json as _json
    errors: list[str] = []
    diff_fields: list[str] = []

    try:
        with (tempfile.TemporaryDirectory() as d1,
              tempfile.TemporaryDirectory() as d2):
            for d in (d1, d2):
                r = PreviewRenderer.from_files(
                    manifest_path=Path(asset_manifest),
                    plan_path=Path(render_plan),
                    output_dir=Path(d),
                    asset_manifest_ref=f"file://{Path(asset_manifest).resolve()}",
                    dry_run=dry_run,
                )
                if dry_run:
                    r.render()
                else:
                    r.verify()

            # Compare render_output.json
            ro1 = _json.loads(Path(d1, "render_output.json").read_bytes())
            ro2 = _json.loads(Path(d2, "render_output.json").read_bytes())
            for field in _diff_json(ro1, ro2, skip=_SKIP_RENDER_OUTPUT_FIELDS):
                diff_fields.append(f"render_output.{field}")

            # Compare render_fingerprint.json (full render only)
            if not dry_run:
                fp1 = _json.loads(Path(d1, "render_fingerprint.json").read_bytes())
                fp2 = _json.loads(Path(d2, "render_fingerprint.json").read_bytes())
                for field in _diff_json(fp1, fp2):
                    diff_fields.append(f"render_fingerprint.{field}")

    except Exception as exc:
        errors.append(str(exc))

    if errors:
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        print("ERROR: audit-render failed", file=sys.stderr)
        return 1

    audit = RenderAudit(
        status="fail" if diff_fields else "pass",
        diff_fields=sorted(diff_fields),
    )
    print(audit.model_dump_json(indent=2))
    return 0 if not diff_fields else 1


def _fingerprint_bytes(out_dir: Path, profile: str = "preview") -> bytes:
    """Run verify() on the minimal fixture; return render_fingerprint.json bytes."""
    manifest, plan = build_minimal_verify_fixture(
        profile=_CLI_TO_PLAN_PROFILE[profile]
    )
    PreviewRenderer(
        manifest, plan,
        output_dir=out_dir,
        asset_manifest_ref="file:///asset_manifest.json",
        dry_run=False,
    ).verify()
    return (out_dir / "render_fingerprint.json").read_bytes()


def cmd_verify(strict: bool = False, profile: str = "preview") -> int:
    """
    System verification export.
    Runs verify() twice on the minimal fixture, compares fingerprint bytes,
    and checks pinned mp4/srt hashes.
    Returns 0 on success, 1 on failure.
    """
    pinned_mp4 = _PINNED_MP4_SHA256.get(profile)
    errors: list[str] = []
    try:
        with (tempfile.TemporaryDirectory() as d1,
              tempfile.TemporaryDirectory() as d2):
            b1 = _fingerprint_bytes(Path(d1), profile=profile)
            b2 = _fingerprint_bytes(Path(d2), profile=profile)

            if b1 != b2:
                errors.append("fingerprint JSON bytes differ between runs")

            fp = json.loads(b1)

            # mp4 check — warn only unless --strict
            if pinned_mp4 and fp.get("mp4_sha256") != pinned_mp4:
                msg = (
                    f"mp4_sha256 mismatch: expected {pinned_mp4}, "
                    f"got {fp.get('mp4_sha256')}"
                )
                if strict:
                    errors.append(msg)
                else:
                    print(f"  WARNING: {msg}", file=sys.stderr)

            # srt check — always hard fail
            if fp.get("srt_sha256") != _PINNED_SRT_SHA256:
                errors.append(
                    f"srt_sha256 mismatch: expected {_PINNED_SRT_SHA256}, "
                    f"got {fp.get('srt_sha256')}"
                )
    except Exception as exc:
        errors.append(str(exc))

    if errors:
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        print("ERROR: video verification failed")
        return 1

    print("OK: video verified")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="video — renderer CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    verify_parser = sub.add_parser("verify", help="Run system verification export")
    verify_parser.add_argument(
        "--strict", action="store_true",
        help="Fail (exit 1) on mp4_sha256 mismatch instead of warning",
    )
    verify_parser.add_argument(
        "--profile", default="preview", choices=["preview", "high"],
        help="Quality profile (default: preview)",
    )
    audit_parser = sub.add_parser("audit-render", help="Detect nondeterminism in a render")
    audit_parser.add_argument("render_plan",    help="Path to RenderPlan JSON")
    audit_parser.add_argument("asset_manifest", help="Path to AssetManifest JSON")
    audit_parser.add_argument(
        "--dry-run", action="store_true",
        help="Compare dry-run outputs only (faster; no ffmpeg call)",
    )
    args = parser.parse_args()
    if args.command == "verify":
        sys.exit(cmd_verify(strict=args.strict, profile=args.profile))
    elif args.command == "audit-render":
        sys.exit(cmd_audit_render(
            args.render_plan, args.asset_manifest, dry_run=args.dry_run,
        ))


if __name__ == "__main__":
    main()
