#!/usr/bin/env python3
"""
Sole renderer CLI — accepts AssetManifest.json and RenderPlan.json, auto-detects
format (native Pydantic or orchestrator-adapter), then invokes PreviewRenderer.

Stdout: full RenderOutput JSON (parseable by callers).
Stderr: error message on failure (exit code 1).

Usage::

    python scripts/render_from_orchestrator.py \\
        --asset-manifest /path/to/AssetManifest.json \\
        --render-plan    /path/to/RenderPlan.json \\
        --out-dir        /tmp/smoke-out
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

_CONTRACTS_SCHEMAS_DIR = Path(__file__).resolve().parents[1] / "third_party" / "contracts" / "schemas"

from schemas.asset_manifest import AssetManifest, Shot, VisualAsset, VOLine
from schemas.render_plan import FallbackConfig, RenderPlan, Resolution
from renderer.preview_local import PreviewRenderer
from verify_contracts import check_schema

# Fallback shot duration (ms) when orchestrator manifest carries no timing data.
_DEFAULT_SHOT_MS = 3_000

# Minimum byte size for a file:// asset to be considered non-stub.
# A 1×1 PNG (the smallest valid PNG) is ~67 bytes; any real image is far larger.
# A WAV with audio data is > 44 bytes (44 = RIFF header only, 0 samples).
# Files at or below this threshold are treated as placeholder stubs.
_MIN_REAL_ASSET_BYTES = 100


# ---------------------------------------------------------------------------
# Contract validation
# ---------------------------------------------------------------------------

def _validate_contract(data: dict, label: str) -> None:
    """Validate *data* against its contract JSON schema.

    The schema is located via the document's own ``schema_id`` field, which
    maps directly into verify_contracts.SCHEMA_MAP
    (e.g. "AssetManifest_final" → AssetManifest_final.v1.json).

    Exits with code 1 and a human-readable message on validation failure.
    Silently passes when schema_id is absent or has no SCHEMA_MAP entry —
    unknown/internal formats are not penalised.
    """
    schema_id = data.get("schema_id")
    if not schema_id:
        return  # no schema_id → can't resolve schema; skip silently

    errors = check_schema(data, schema_id, _CONTRACTS_SCHEMAS_DIR)
    if errors:
        print(f"Contract validation FAILED for {label} (schema_id={schema_id!r}):",
              file=sys.stderr)
        for err in errors:
            print(f"  {err}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Schema adapters
# ---------------------------------------------------------------------------

def _adapt_manifest(raw: dict, timing_lock_hash: str) -> AssetManifest:
    """
    Translate orchestrator AssetManifest JSON → renderer AssetManifest model.

    Orchestrator layout
    -------------------
    backgrounds[]        one entry per scene; carries scene_id and bg_id
    character_packs[]    flat list of character packs (not scene-bound)
    vo_items[]           each item_id encodes the scene_id it belongs to
                         (e.g. "vo-scene-001-commander-000")

    Renderer layout
    ---------------
    shots[]              one Shot per scene, each containing visual_assets and vo_lines
    timing_lock_hash     taken from the companion RenderPlan (absent in orchestrator manifest)
    """
    shots: list[Shot] = []

    for bg in raw.get("backgrounds", []):
        scene_id = bg["scene_id"]

        # Visual assets: background first, then all character packs.
        visual_assets: list[VisualAsset] = [
            VisualAsset(
                asset_id=bg["bg_id"],
                role="background",
                placeholder=bg.get("is_placeholder", False),
            )
        ]
        for cp in raw.get("character_packs", []):
            visual_assets.append(
                VisualAsset(
                    asset_id=cp["pack_id"],
                    role="character",
                    placeholder=cp.get("is_placeholder", False),
                )
            )

        # VO lines: match items whose item_id contains this scene_id.
        vo_lines: list[VOLine] = [
            VOLine(
                line_id=vo["item_id"],
                speaker_id=vo["speaker_id"],
                text=vo["text"],
            )
            for vo in raw.get("vo_items", [])
            if scene_id in vo["item_id"]
        ]

        shots.append(
            Shot(
                shot_id=scene_id,
                duration_ms=_DEFAULT_SHOT_MS,
                visual_assets=visual_assets,
                vo_lines=vo_lines,
            )
        )

    return AssetManifest(
        schema_version=raw.get("schema_version", "1.0.0"),
        manifest_id=raw["manifest_id"],
        project_id=raw["project_id"],
        shotlist_ref=raw["shotlist_ref"],
        timing_lock_hash=timing_lock_hash,
        shots=shots,
    )


def _adapt_plan(raw: dict, render_plan_path: Path) -> RenderPlan:
    """
    Translate orchestrator RenderPlan JSON → renderer RenderPlan model.

    Key adaptations
    ---------------
    resolution  "WxH" string  →  Resolution(width, height, aspect)
    asset_manifest_ref         set to file:// URI of the render-plan file itself
                               so that RenderOutput.render_plan_ref equals the
                               absolute path of the render plan (requirement #2).
    asset_resolutions          empty — all orchestrator URIs are placeholder://;
                               renderer falls back to generated placeholders.
    audio_resolutions          empty — no TTS audio in Phase 0 demo run.
    """
    width_str, height_str = raw["resolution"].split("x", 1)
    resolution = Resolution(
        width=int(width_str),
        height=int(height_str),
        aspect=raw["aspect_ratio"],
    )

    return RenderPlan(
        schema_version=raw.get("schema_version", "1.0.0"),
        plan_id=raw["plan_id"],
        project_id=raw["project_id"],
        profile=raw["profile"],
        resolution=resolution,
        fps=raw["fps"],
        # Setting asset_manifest_ref to the render-plan file URI causes
        # PreviewRenderer to write this value into RenderOutput.render_plan_ref
        # (see preview_local.py line 151: render_plan_ref=self.plan.asset_manifest_ref).
        asset_manifest_ref=f"file://{render_plan_path.resolve()}",
        timing_lock_hash=raw["timing_lock_hash"],
        asset_resolutions={},
        audio_resolutions={},
    )


def _adapt_manifest_final(raw: dict, timing_lock_hash: str) -> AssetManifest:
    """
    Translate AssetManifest_final / AssetManifest.media JSON → renderer AssetManifest model.

    Final / media layout
    --------------------
    items[]  flat list of resolved assets, each carrying:
             asset_id    e.g. "bg-scene-001", "char-analyst",
                              "vo-scene-001-commander-000"
             asset_type  "background" | "character" | "prop" | "vo" | …
             uri         resolved URI ("file://" or "placeholder://")
             is_placeholder  bool

    Shot reconstruction
    -------------------
    One Shot per background item.  scene_id is derived by stripping the "bg-"
    prefix (e.g. "bg-scene-001" → "scene-001").  Characters are shared across
    all shots.  VO items are assigned to the shot whose scene_id appears as a
    substring in the VO asset_id.  Speaker is the second-to-last dash segment
    of the VO asset_id ("vo-scene-001-commander-000" → "commander"); VO text is
    unavailable in this format so an empty string is stored.
    """
    items = raw.get("items", [])

    backgrounds = [i for i in items if i["asset_type"] == "background"]
    characters  = [i for i in items if i["asset_type"] == "character"]
    vo_items    = [i for i in items if i["asset_type"] == "vo"]

    shots: list[Shot] = []
    for bg in backgrounds:
        bg_id    = bg["asset_id"]
        scene_id = bg_id[len("bg-"):] if bg_id.startswith("bg-") else bg_id

        visual_assets: list[VisualAsset] = [
            VisualAsset(
                asset_id=bg_id,
                role="background",
                asset_uri=bg.get("uri"),
                placeholder=bg.get("is_placeholder", False),
            )
        ]
        for cp in characters:
            visual_assets.append(
                VisualAsset(
                    asset_id=cp["asset_id"],
                    role="character",
                    asset_uri=cp.get("uri"),
                    placeholder=cp.get("is_placeholder", False),
                )
            )

        vo_lines: list[VOLine] = []
        for vo in vo_items:
            vo_id = vo["asset_id"]
            if scene_id not in vo_id:
                continue
            parts     = vo_id.split("-")
            speaker_id = parts[-2] if len(parts) >= 2 else "unknown"
            vo_lines.append(VOLine(line_id=vo_id, speaker_id=speaker_id, text=""))

        shots.append(
            Shot(
                shot_id=scene_id,
                duration_ms=_DEFAULT_SHOT_MS,
                visual_assets=visual_assets,
                vo_lines=vo_lines,
            )
        )

    return AssetManifest(
        schema_version=raw.get("schema_version", "1.0.0"),
        manifest_id=raw["manifest_id"],
        project_id=raw.get("project_id", raw["manifest_id"]),
        shotlist_ref=raw.get("shotlist_ref", ""),
        timing_lock_hash=timing_lock_hash,
        shots=shots,
    )


def _is_stub_file(uri: str) -> bool:
    """
    Return True when a file:// URI points to a file that is too small to be
    real media (i.e. a stub / empty placeholder written by test tooling).

    A 45-byte "PNG" carries only magic bytes + a bare IHDR + empty IDAT + IEND;
    a 44-byte WAV is the RIFF header alone with zero audio samples.  Both are
    indistinguishable from placeholder://  at render time because the renderer
    falls back to Pillow-generated images when Pillow cannot decode the file.

    Does not raise — returns False for any IO error so callers stay simple.
    """
    if not uri.startswith("file://"):
        return False
    try:
        return Path(uri[len("file://"):]).stat().st_size <= _MIN_REAL_ASSET_BYTES
    except OSError:
        return False


def _collect_placeholders(
    manifest: AssetManifest, plan: RenderPlan
) -> list[tuple[str, str, str]]:
    """
    Return (shot_id, asset_id, uri) for every visual slot whose resolved URI
    is absent or starts with 'placeholder://'.

    Does NOT call path.exists() — avoids blocking on unavailable network/FUSE mounts.
    Stub file:// assets (size ≤ _MIN_REAL_ASSET_BYTES) are included alongside
    placeholder:// URIs.
    """
    out: list[tuple[str, str, str]] = []
    for shot in manifest.shots:
        for asset in shot.visual_assets:
            uri = plan.asset_resolutions.get(asset.asset_id) or asset.asset_uri
            if not uri or uri.startswith("placeholder://") or _is_stub_file(uri):
                out.append((shot.shot_id, asset.asset_id, uri or "<no uri>"))
    return out



# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sole renderer CLI: invoke PreviewRenderer and print RenderOutput JSON."
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

    try:
        raw_manifest = json.loads(args.asset_manifest.read_text(encoding="utf-8"))
        raw_plan = json.loads(args.render_plan.read_text(encoding="utf-8"))

        # Validate inputs against canonical contracts before doing any work.
        _validate_contract(raw_manifest, f"asset manifest ({args.asset_manifest.name})")
        _validate_contract(raw_plan,     f"render plan ({args.render_plan.name})")

        # Format auto-detect:
        #   native Pydantic      → top-level "shots" key
        #   orchestrator draft   → "backgrounds" / "character_packs" / "vo_items"
        #   final / media        → flat "items" list (AssetManifest_final or
        #                          AssetManifest.media; no scene structure, URIs resolved)
        if "shots" in raw_manifest:
            manifest = AssetManifest.model_validate(raw_manifest)
            plan = RenderPlan.model_validate(raw_plan)
        elif "items" in raw_manifest:
            manifest = _adapt_manifest_final(raw_manifest, raw_plan["timing_lock_hash"])
            plan = _adapt_plan(raw_plan, args.render_plan)
        else:
            manifest = _adapt_manifest(raw_manifest, raw_plan["timing_lock_hash"])
            plan = _adapt_plan(raw_plan, args.render_plan)

        # Guard 1: no shots at all (format mismatch or empty backgrounds list)
        if not manifest.shots:
            print(
                "No shots found in the manifest.\n"
                "Check your AssetManifest format — expected 'shots' (native) or\n"
                "'backgrounds'/'character_packs'/'vo_items' (orchestrator) keys.",
                file=sys.stderr,
            )
            sys.exit(0)

        # Guard 2: any visual slot is a placeholder URI or stub file.
        # Collect all offending slots first; if there are any, list them and
        # exit — the user must fix every placeholder before a render is valid.
        # FFmpeg is only reached when this list is empty (all assets are real).
        slots = _collect_placeholders(manifest, plan)
        if slots:
            print(
                f"{len(slots)} placeholder/stub slot(s) found across "
                f"{len(manifest.shots)} shot(s) — fix all of them before rendering.\n"
                f"(Stub = file:// asset ≤ {_MIN_REAL_ASSET_BYTES} bytes; "
                "these are test fixtures, not real media.)",
                file=sys.stderr,
            )
            for shot_id, asset_id, uri in slots:
                print(f"  [{shot_id}]  {asset_id}  →  {uri}", file=sys.stderr)
            sys.exit(0)

        args.out_dir.mkdir(parents=True, exist_ok=True)

        asset_manifest_ref = f"file://{args.asset_manifest.resolve()}"
        if args.verify:
            fp = PreviewRenderer(
                manifest, plan, output_dir=args.out_dir,
                asset_manifest_ref=asset_manifest_ref,
                dry_run=False,
            ).verify()
            print(fp.model_dump_json(indent=2))
        else:
            result = PreviewRenderer(
                manifest, plan, output_dir=args.out_dir,
                asset_manifest_ref=asset_manifest_ref,
                dry_run=args.dry_run,
            ).render()
            # Validate the produced RenderOutput against its contract schema.
            # Skipped in dry-run: video_uri / captions_uri are null there and the
            # contract schema requires strings (no partial-output contract exists yet).
            if not args.dry_run:
                _validate_contract(
                    json.loads(result.model_dump_json()),
                    "render output",
                )
            print(result.model_dump_json(indent=2))

    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
