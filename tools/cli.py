#!/usr/bin/env python3
"""
video — top-level renderer CLI (pip-installable entry point).

Subcommands
-----------
  video render        Canonical pipeline render (§41.4 interface)
  video verify        System determinism verification
  video audit-render  Nondeterminism detector (render twice + diff)

sys.path is patched so that the flat imports used by renderer/, schemas/,
and tests/ resolve correctly from the installed package directory.
"""
from __future__ import annotations

import sys
from pathlib import Path

# When installed via pip, __file__ is inside site-packages/tools/.
# Adding that directory to sys.path lets the sibling sub-packages
# (renderer, schemas, tests) be imported with their existing flat-import style.
_PKG_DIR = Path(__file__).resolve().parent
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))

# Contracts tools (verify_contracts module) live in third_party/contracts/tools/
_REPO_ROOT = _PKG_DIR.parent
_CONTRACTS_TOOLS = _REPO_ROOT / "third_party" / "contracts" / "tools"
_CONTRACTS_SCHEMAS_DIR = _REPO_ROOT / "third_party" / "contracts" / "schemas"
if str(_CONTRACTS_TOOLS) not in sys.path:
    sys.path.insert(0, str(_CONTRACTS_TOOLS))

import argparse
import json
import shutil
import tempfile

from tests._fixture_builders import build_minimal_verify_fixture
from renderer.preview_local import PreviewRenderer
from schemas.asset_manifest import AssetManifest, Shot, VisualAsset, VOLine
from schemas.render_plan import RenderPlan, Resolution
from schemas.render_output import RenderAudit
from verify_contracts import check_schema

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SKIP_RENDER_OUTPUT_FIELDS = frozenset({
    "rendered_at",
    "video_uri",
    "captions_uri",
    "audio_stems_uri",
    "outputs",
})

_PINNED_SRT_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
_PINNED_MP4_SHA256: dict[str, str] = {
    "preview": "b4a44e354dc6e8808a94a59b7bd402e0496e3d1489223a20a92132a7c8ecd6a9",
    "high": "5e41afd474b4d812d3bcabb226f3effea0f6cdce277eaba48d6d5d2fce0dcaf8",
}

_CLI_TO_PLAN_PROFILE: dict[str, str] = {
    "preview": "preview_local",
    "high": "high",
}

# Fallback shot duration (ms) used when the manifest carries no timing data.
_DEFAULT_SHOT_MS = 3_000

# Stub-file threshold: file:// assets at or below this byte size are treated
# as placeholder stubs (a 1×1 PNG is ~67 bytes; a RIFF header-only WAV is 44 bytes).
_MIN_REAL_ASSET_BYTES = 100


# =============================================================================
# Shared helpers
# (used by cmd_render here and imported by scripts/render_from_orchestrator.py)
# =============================================================================

def _validate_contract(data: dict, label: str) -> None:
    """Validate *data* against its contract JSON schema (keyed by schema_id).

    Exits with code 1 on validation failure.  Silently passes when schema_id is
    absent — unknown/internal formats are not penalised.
    """
    schema_id = data.get("schema_id")
    if not schema_id:
        return
    errors = check_schema(data, schema_id, _CONTRACTS_SCHEMAS_DIR)
    if errors:
        print(
            f"Contract validation FAILED for {label} (schema_id={schema_id!r}):",
            file=sys.stderr,
        )
        for err in errors:
            print(f"  {err}", file=sys.stderr)
        sys.exit(1)


def _is_stub_file(uri: str) -> bool:
    """Return True when a file:// URI points to a file too small to be real media."""
    if not uri.startswith("file://"):
        return False
    try:
        return Path(uri[len("file://"):]).stat().st_size <= _MIN_REAL_ASSET_BYTES
    except OSError:
        return False


def _adapt_manifest(raw: dict, timing_lock_hash: str) -> AssetManifest:
    """Translate orchestrator-draft AssetManifest JSON → renderer AssetManifest model.

    Orchestrator layout: backgrounds[], character_packs[], vo_items[].
    """
    shots: list[Shot] = []
    for bg in raw.get("backgrounds", []):
        scene_id = bg["scene_id"]
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


def _adapt_manifest_final(raw: dict, timing_lock_hash: str) -> AssetManifest:
    """Translate AssetManifest_final / AssetManifest.media JSON → renderer AssetManifest.

    Final/media layout: flat items[] list with asset_type + resolved URIs.
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
            parts = vo_id.split("-")
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


def _adapt_plan(raw: dict, render_plan_path: Path) -> RenderPlan:
    """Translate orchestrator RenderPlan JSON → renderer RenderPlan model.

    asset_resolutions only carries file:// URIs; placeholder:// entries are
    excluded so the renderer falls back to placeholder generation for those
    assets.  When the plan also contains shots[], _build_shots_from_plan uses
    a separate full URI map (see cmd_render) that includes placeholder:// so
    it can set VisualAsset.placeholder correctly.
    """
    width_str, height_str = raw["resolution"].split("x", 1)
    resolution = Resolution(
        width=int(width_str),
        height=int(height_str),
        aspect=raw["aspect_ratio"],
    )
    asset_resolutions = {
        a["asset_id"]: a["uri"]
        for a in raw.get("resolved_assets", [])
        if a.get("uri") and not a["uri"].startswith("placeholder://")
    }
    return RenderPlan(
        schema_version=raw.get("schema_version", "1.0.0"),
        plan_id=raw["plan_id"],
        project_id=raw["project_id"],
        profile=raw["profile"],
        resolution=resolution,
        fps=raw["fps"],
        asset_manifest_ref=f"file://{render_plan_path.resolve()}",
        timing_lock_hash=raw["timing_lock_hash"],
        asset_resolutions=asset_resolutions,
        audio_resolutions={},
    )


def _build_shots_from_plan(
    plan_shots: list[dict],
    all_asset_uris: dict[str, str | None],
) -> list[Shot]:
    """Build renderer Shot objects from RenderPlan.shots[].

    RenderPlan.shots[] is the authoritative ordered shot sequence emitted by
    the orchestrator plan builder.  Each entry carries fully resolved asset_id
    values and per-shot VO lines, replacing the background-grouping heuristic
    used by _adapt_manifest_final when the plan lacks explicit shot data.

    all_asset_uris: {asset_id → uri} built from resolved_assets[] *including*
    placeholder:// entries, so VisualAsset.placeholder is set correctly and
    the renderer knows which assets need synthetic placeholder generation.
    """
    shots: list[Shot] = []
    for s in plan_shots:
        # ── Background ────────────────────────────────────────────────────────
        bg_id = s.get("background_asset_id")
        visual_assets: list[VisualAsset] = []
        if bg_id:
            bg_uri = all_asset_uris.get(bg_id)
            visual_assets.append(
                VisualAsset(
                    asset_id=bg_id,
                    role="background",
                    asset_uri=bg_uri,
                    placeholder=not bg_uri or bg_uri.startswith("placeholder://"),
                )
            )

        # ── Characters ────────────────────────────────────────────────────────
        for char_id in s.get("character_asset_ids", []):
            char_uri = all_asset_uris.get(char_id)
            visual_assets.append(
                VisualAsset(
                    asset_id=char_id,
                    role="character",
                    asset_uri=char_uri,
                    placeholder=not char_uri or char_uri.startswith("placeholder://"),
                )
            )

        # ── VO lines ──────────────────────────────────────────────────────────
        vo_lines: list[VOLine] = []
        for v in s.get("vo_lines", []):
            vo_lines.append(
                VOLine(
                    line_id=v["line_id"],
                    speaker_id=v["speaker_id"],
                    text=v["text"],
                    timeline_in_ms=v["timeline_in_ms"],
                    timeline_out_ms=v["timeline_out_ms"],
                )
            )

        shots.append(
            Shot(
                shot_id=s["shot_id"],
                duration_ms=s["duration_ms"],
                visual_assets=visual_assets,
                vo_lines=vo_lines,
            )
        )
    return shots


# =============================================================================
# cmd_render  (video render subcommand — §41.4 canonical interface)
# =============================================================================

def cmd_render(
    manifest_path: Path,
    plan_path: Path,
    out_path: Path,
    video_path: Path,
    srt_path: Path | None = None,
    dry_run: bool = False,
) -> int:
    """Render AssetManifest + RenderPlan → mp4 + srt + RenderOutput.json.

    Validates inputs and output against pinned contracts (§41.4 rules 3–4).
    Prints RenderOutput JSON to stdout on success.
    Returns exit code: 0 on success, 1 on failure.
    """
    if srt_path is None:
        srt_path = video_path.with_suffix(".srt")

    try:
        raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        raw_plan = json.loads(plan_path.read_text(encoding="utf-8"))

        # Validate inputs against contracts before doing any work (§41.4 rule 3).
        _validate_contract(raw_manifest, f"asset manifest ({manifest_path.name})")
        _validate_contract(raw_plan, f"render plan ({plan_path.name})")

        # Full URI map from resolved_assets[] — includes placeholder:// entries
        # so _build_shots_from_plan can mark unresolved assets correctly.
        all_asset_uris: dict[str, str | None] = {
            a["asset_id"]: a.get("uri")
            for a in raw_plan.get("resolved_assets", [])
            if a.get("asset_id")
        }

        # Auto-detect manifest format and adapt to renderer models.
        #
        #   Priority 1 — native Pydantic manifest  ("shots" key present)
        #     → manifest and plan are in renderer-native format; validate directly.
        #
        #   Priority 2 — plan carries shots[]  (orchestrator §41.4 canonical path)
        #     → use RenderPlan.shots[] as the authoritative ordered shot sequence;
        #       manifest provides envelope metadata only (manifest_id, project_id).
        #       Applies regardless of whether the manifest is "items" or draft format.
        #
        #   Priority 3 — final/media manifest  ("items" key, no plan shots[])
        #     → infer shots from background items (one bg item = one shot, 3 s each).
        #       Fallback for plans produced before shots[] was introduced.
        #
        #   Priority 4 — orchestrator draft manifest  (fallback)
        #     → infer shots from backgrounds[] / character_packs[] / vo_items[].
        if "shots" in raw_manifest:
            manifest = AssetManifest.model_validate(raw_manifest)
            plan = RenderPlan.model_validate(raw_plan)
        elif raw_plan.get("shots"):
            shots = _build_shots_from_plan(raw_plan["shots"], all_asset_uris)
            manifest = AssetManifest(
                schema_version=raw_manifest.get("schema_version", "1.0.0"),
                manifest_id=raw_manifest.get(
                    "manifest_id", raw_plan.get("plan_id", "unknown")
                ),
                project_id=raw_manifest.get(
                    "project_id", raw_plan.get("project_id", "unknown")
                ),
                shotlist_ref=raw_manifest.get("shotlist_ref", ""),
                timing_lock_hash=raw_plan["timing_lock_hash"],
                shots=shots,
            )
            plan = _adapt_plan(raw_plan, plan_path)
        elif "items" in raw_manifest:
            manifest = _adapt_manifest_final(raw_manifest, raw_plan["timing_lock_hash"])
            plan = _adapt_plan(raw_plan, plan_path)
        else:
            manifest = _adapt_manifest(raw_manifest, raw_plan["timing_lock_hash"])
            plan = _adapt_plan(raw_plan, plan_path)

        if not manifest.shots:
            print(
                "No shots found in manifest — check AssetManifest format.",
                file=sys.stderr,
            )
            return 1

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            result = PreviewRenderer(
                manifest, plan,
                output_dir=tmp_dir,
                asset_manifest_ref=f"file://{manifest_path.resolve()}",
                dry_run=dry_run,
            ).render()

            # Validate RenderOutput against contract before writing to disk.
            # Skipped in dry-run: video_uri/captions_uri are null there and the
            # contract requires strings (no partial-output contract exists yet).
            if not dry_run:
                _validate_contract(
                    json.loads(result.model_dump_json()),
                    "render output",
                )

            # Write RenderOutput.json to the explicit --out path.
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")

            # Move mp4 and srt to their explicit output paths.
            if not dry_run:
                video_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(tmp_dir / "output.mp4"), str(video_path))
                srt_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(tmp_dir / "output.srt"), str(srt_path))

        print(result.model_dump_json(indent=2))
        return 0

    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


# =============================================================================
# cmd_audit_render
# =============================================================================

def _diff_json(
    a: dict,
    b: dict,
    *,
    skip: frozenset[str] = frozenset(),
    prefix: str = "",
) -> list[str]:
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

            ro1 = _json.loads(Path(d1, "render_output.json").read_bytes())
            ro2 = _json.loads(Path(d2, "render_output.json").read_bytes())
            for field in _diff_json(ro1, ro2, skip=_SKIP_RENDER_OUTPUT_FIELDS):
                diff_fields.append(f"render_output.{field}")

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


# =============================================================================
# cmd_verify
# =============================================================================

def _fingerprint_bytes(out_dir: Path, profile: str = "preview") -> bytes:
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

            if pinned_mp4 and fp.get("mp4_sha256") != pinned_mp4:
                msg = (
                    f"mp4_sha256 mismatch: expected {pinned_mp4}, "
                    f"got {fp.get('mp4_sha256')}"
                )
                if strict:
                    errors.append(msg)
                else:
                    print(f"  WARNING: {msg}", file=sys.stderr)

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


# =============================================================================
# CLI entry point
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="video",
        description="video — deterministic preview renderer CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── video render ──────────────────────────────────────────────────────────
    render_parser = sub.add_parser(
        "render",
        help="Render AssetManifest + RenderPlan → mp4 + srt + RenderOutput.json",
        description=(
            "Canonical pipeline render (§41.4).\n"
            "Validates inputs and output against pinned contracts. "
            "Prints RenderOutput JSON to stdout."
        ),
    )
    render_parser.add_argument(
        "--manifest", type=Path, required=True, metavar="PATH",
        help="Path to AssetManifest.final.json",
    )
    render_parser.add_argument(
        "--plan", type=Path, required=True, metavar="PATH",
        help="Path to RenderPlan.json",
    )
    render_parser.add_argument(
        "--out", type=Path, required=True, metavar="PATH",
        help="Output path for RenderOutput.json",
    )
    render_parser.add_argument(
        "--video", type=Path, required=True, metavar="PATH",
        help="Output path for output.mp4",
    )
    render_parser.add_argument(
        "--srt", type=Path, default=None, metavar="PATH",
        help="Output path for output.srt (default: <video-path with .srt extension>)",
    )
    render_parser.add_argument(
        "--dry-run", action="store_true",
        help="Validate inputs and write RenderOutput only; skip mp4/srt",
    )

    # ── video verify ─────────────────────────────────────────────────────────
    verify_parser = sub.add_parser("verify", help="Run system verification export")
    verify_parser.add_argument(
        "--strict", action="store_true",
        help="Fail (exit 1) on mp4_sha256 mismatch instead of warning",
    )
    verify_parser.add_argument(
        "--profile", default="preview", choices=["preview", "high"],
        help="Quality profile (default: preview)",
    )

    # ── video audit-render ────────────────────────────────────────────────────
    audit_parser = sub.add_parser("audit-render", help="Detect nondeterminism in a render")
    audit_parser.add_argument("render_plan",    help="Path to RenderPlan JSON")
    audit_parser.add_argument("asset_manifest", help="Path to AssetManifest JSON")
    audit_parser.add_argument(
        "--dry-run", action="store_true",
        help="Compare dry-run outputs only (faster; no ffmpeg call)",
    )

    args = parser.parse_args()

    if args.command == "render":
        sys.exit(cmd_render(
            manifest_path=args.manifest,
            plan_path=args.plan,
            out_path=args.out,
            video_path=args.video,
            srt_path=args.srt,
            dry_run=args.dry_run,
        ))
    elif args.command == "verify":
        sys.exit(cmd_verify(strict=args.strict, profile=args.profile))
    elif args.command == "audit-render":
        sys.exit(cmd_audit_render(
            args.render_plan, args.asset_manifest, dry_run=args.dry_run,
        ))


if __name__ == "__main__":
    main()
