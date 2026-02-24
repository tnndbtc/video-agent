#!/usr/bin/env python3
"""
Backward-compatible render entry point (--out-dir interface).

All render logic delegates to tools.cli.cmd_render and helpers.
For new integrations prefer the canonical form::

    video render \\
        --manifest AssetManifest.final.json \\
        --plan     RenderPlan.json \\
        --out      RenderOutput.json \\
        --video    output.mp4

Stdout: full RenderOutput JSON (parseable by callers).
Stderr: error message on failure (exit code 1).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_TOOLS_ROOT = Path(__file__).resolve().parents[1] / "tools"
if str(_TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(_TOOLS_ROOT))

_CONTRACTS_TOOLS = Path(__file__).resolve().parents[1] / "third_party" / "contracts" / "tools"
if str(_CONTRACTS_TOOLS) not in sys.path:
    sys.path.insert(0, str(_CONTRACTS_TOOLS))

from cli import (
    cmd_render,
    _adapt_manifest,
    _adapt_manifest_final,
    _adapt_plan,
    _validate_contract,
)
from schemas.asset_manifest import AssetManifest
from schemas.render_plan import RenderPlan
from renderer.preview_local import PreviewRenderer


def _run_verify(args: argparse.Namespace) -> None:
    """Handle legacy --verify path: full render + emit RenderFingerprint JSON to stdout."""
    raw_manifest = json.loads(args.asset_manifest.read_text(encoding="utf-8"))
    raw_plan = json.loads(args.render_plan.read_text(encoding="utf-8"))

    _validate_contract(raw_manifest, f"asset manifest ({args.asset_manifest.name})")
    _validate_contract(raw_plan, f"render plan ({args.render_plan.name})")

    if "shots" in raw_manifest:
        manifest = AssetManifest.model_validate(raw_manifest)
        plan = RenderPlan.model_validate(raw_plan)
    elif "items" in raw_manifest:
        manifest = _adapt_manifest_final(raw_manifest, raw_plan["timing_lock_hash"])
        plan = _adapt_plan(raw_plan, args.render_plan)
    else:
        manifest = _adapt_manifest(raw_manifest, raw_plan["timing_lock_hash"])
        plan = _adapt_plan(raw_plan, args.render_plan)

    fp = PreviewRenderer(
        manifest, plan,
        output_dir=args.out_dir,
        asset_manifest_ref=f"file://{args.asset_manifest.resolve()}",
        dry_run=False,
    ).verify()
    print(fp.model_dump_json(indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Backward-compatible render entry point (--out-dir interface).\n"
            "For new integrations prefer: video render --manifest ... --plan ... "
            "--out ... --video ..."
        )
    )
    parser.add_argument(
        "--asset-manifest",
        type=Path,
        required=True,
        metavar="PATH",
        help="Path to AssetManifest.json (native Pydantic or orchestrator format)",
    )
    parser.add_argument(
        "--render-plan",
        type=Path,
        required=True,
        metavar="PATH",
        help="Path to RenderPlan.json (native Pydantic or orchestrator format)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        metavar="PATH",
        help="Output directory for output.mp4, output.srt, render_output.json",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help=(
            "Validate inputs and write render_output.json with effective_settings only; "
            "no mp4 or srt is produced."
        ),
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        default=False,
        help=(
            "Run dry-run validation then full render; emit render_fingerprint.json. "
            "Stdout: fingerprint JSON (no timestamps). Cannot be combined with --dry-run."
        ),
    )
    args = parser.parse_args()

    if args.verify and args.dry_run:
        print("ERROR: --verify and --dry-run are mutually exclusive", file=sys.stderr)
        sys.exit(1)

    args.out_dir.mkdir(parents=True, exist_ok=True)

    if args.verify:
        _run_verify(args)
        return

    sys.exit(cmd_render(
        manifest_path=args.asset_manifest,
        plan_path=args.render_plan,
        out_path=args.out_dir / "render_output.json",
        video_path=args.out_dir / "output.mp4",
        srt_path=args.out_dir / "output.srt",
        dry_run=args.dry_run,
    ))


if __name__ == "__main__":
    main()
