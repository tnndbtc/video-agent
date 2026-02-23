"""
Phase 0 Preview Renderer — profile: preview_local.

Inputs:  AssetManifest (§5.7) + RenderPlan (§5.8, profile=preview_local)
Outputs: output.mp4 + output.srt + render_output.json  (§5.9)

Design guarantees:
  1. Deterministic — same inputs produce bit-identical video output
     (requires identical ffmpeg version; see README.md §ffmpeg-version).
  2. Complete — missing visual asset slots are filled with placeholder PNGs;
     the render never aborts due to a missing asset.
  3. Local-only — zero external calls; ffmpeg is the only subprocess.
  4. Schema-validated — outputs a RenderOutput that round-trips through
     the Pydantic model.

Phase 0 scope:
  - Static images only (no Ken Burns / zoompan).
  - Cut transitions only (no crossfade).
  - Captions: sidecar .srt only (no burned-in subtitles).
  - Audio: silence (-an) or optional background music track.
  - No distributed queue; sequential single-process execution.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import logging
import subprocess
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from schemas.asset_manifest import AssetManifest, Shot
from schemas.render_plan import RenderPlan
from schemas.render_output import (
    EffectiveSettings,
    Lineage,
    OutputArtifact,
    OutputHashes,
    Producer,
    Provenance,
    RenderFingerprint,
    RenderOutput,
)
from renderer.captions import write_srt
from renderer.ffmpeg_runner import FFmpegError, run_ffmpeg, validate_ffmpeg
from renderer.placeholder import generate_placeholder

logger = logging.getLogger(__name__)

_PROFILE_SETTINGS: dict[str, dict[str, str]] = {
    "preview": {"crf": "28", "preset": "medium"},
    "high":    {"crf": "18", "preset": "slow"},
}
_PROFILE_ALIASES: dict[str, str] = {
    "preview_local": "preview",   # Phase-0 backward-compat alias
}


def _normalize_profile(raw: str) -> str:
    return _PROFILE_ALIASES.get(raw, raw)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

class PreviewRenderer:
    """
    Deterministic preview renderer for profile=preview_local.

    Usage::

        from renderer.preview_local import PreviewRenderer
        from schemas.asset_manifest import AssetManifest
        from schemas.render_plan import RenderPlan

        manifest = AssetManifest.model_validate_json(manifest_path.read_text())
        plan     = RenderPlan.model_validate_json(plan_path.read_text())
        result   = PreviewRenderer(manifest, plan, output_dir=Path("/tmp/out")).render()
        print(result.model_dump_json(indent=2))
    """

    def __init__(
        self,
        manifest: AssetManifest,
        plan: RenderPlan,
        output_dir: Path,
        request_id: Optional[str] = None,
        asset_manifest_ref: str = "",   # file:// URI of the source manifest
        dry_run: bool = False,
    ) -> None:
        _norm = _normalize_profile(plan.profile)
        if _norm not in _PROFILE_SETTINGS:
            raise ValueError(
                f"PreviewRenderer: unsupported profile {plan.profile!r}. "
                f"Supported: {sorted(_PROFILE_SETTINGS)} "
                f"(aliases: {sorted(_PROFILE_ALIASES)})"
            )
        self._profile = _norm   # canonical name; used throughout
        if manifest.timing_lock_hash != plan.timing_lock_hash:
            raise ValueError(
                f"timing_lock_hash mismatch between AssetManifest "
                f"({manifest.timing_lock_hash!r}) and RenderPlan "
                f"({plan.timing_lock_hash!r}). "
                f"Ensure both were produced from the same ShotList."
            )

        self.manifest = manifest
        self.plan = plan
        self.output_dir = Path(output_dir)
        self._asset_manifest_ref = asset_manifest_ref
        self.dry_run = dry_run

        # Pre-compute canonical lineage hashes once; reused by render() to
        # avoid double-serialisation and to derive stable IDs.
        self._manifest_hash = _canonical_json_hash(self.manifest.model_dump())
        self._plan_hash = _canonical_json_hash(self.plan.model_dump())
        # Stable render/request identity derived from inputs, not a random UUID.
        self._derived_id = hashlib.sha256(
            f"{self._manifest_hash}:{self._plan_hash}".encode("utf-8")
        ).hexdigest()
        self.request_id = request_id or self._derived_id

        # Fail fast: validate ffmpeg presence and version before any work starts.
        # Skip the check in dry-run mode — no subprocess will be executed.
        self._ffmpeg_version = "dry-run" if dry_run else validate_ffmpeg()
        self._placeholder_count = 0

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    @classmethod
    def from_files(
        cls,
        manifest_path: Path,
        plan_path: Path,
        **kwargs,
    ) -> "PreviewRenderer":
        """Load manifest + plan from JSON files; raise on missing files."""
        for path in (manifest_path, plan_path):
            if not Path(path).exists():
                raise FileNotFoundError(
                    f"ERROR: missing required input: {Path(path).name}"
                )
        manifest = AssetManifest.model_validate_json(Path(manifest_path).read_text())
        plan = RenderPlan.model_validate_json(Path(plan_path).read_text())
        return cls(manifest, plan, **kwargs)

    def render(self) -> RenderOutput:
        """
        Execute the full render pipeline and return a RenderOutput.

        Writes to output_dir:
          output.mp4         — encoded video
          output.srt         — SRT captions (empty if no VO lines)
          render_output.json — serialised RenderOutput for the artifact registry

        In dry-run mode, only render_output.json is written; no mp4 or srt
        is produced.  See _dry_run_output() for details.
        """
        # --- Early exit for dry-run ---
        if self.dry_run:
            return self._dry_run_output()

        self.output_dir.mkdir(parents=True, exist_ok=True)
        placeholder_dir = self.output_dir / ".placeholders"
        placeholder_dir.mkdir(exist_ok=True)

        ffmpeg_version = self._ffmpeg_version   # already validated at __init__
        logger.info(
            "PreviewRenderer | project=%s | ffmpeg=%s | shots=%d",
            self.manifest.project_id,
            ffmpeg_version,
            len(self.manifest.shots),
        )

        # Lineage hashes: canonical JSON (sorted keys, compact separators, UTF-8).
        # Pre-computed in __init__ to guarantee the same hash as the derived IDs.
        manifest_hash = self._manifest_hash
        plan_hash = self._plan_hash

        # Step 1 — resolve or generate one visual input per shot.
        shot_inputs = self._resolve_shot_inputs(placeholder_dir)

        # Step 2 — build and execute the ffmpeg concat command.
        output_mp4 = self.output_dir / "output.mp4"
        self._run_concat(shot_inputs, output_mp4)

        # Step 3 — generate SRT captions.
        output_srt = self.output_dir / "output.srt"
        write_srt(self.manifest, output_srt)

        # Step 4 — compute content hashes.
        video_hash = _sha256_file(output_mp4)
        captions_hash = _sha256_text(output_srt.read_text(encoding="utf-8"))

        # Step 5 — assemble RenderOutput.
        _ps = _PROFILE_SETTINGS[self._profile]
        effective = EffectiveSettings(
            resolution=f"{self.plan.resolution.width}x{self.plan.resolution.height}",
            fps=str(self.plan.fps),
            audio_rate="aac" if _resolve_music(self.manifest) else "none",
            encoder="libx264",
            crf=_ps["crf"],
            preset=_ps["preset"],
            profile=self._profile,
        )
        result = RenderOutput(
            schema_version="0.0.1",
            schema_id="RenderOutput",
            output_id=self._derived_id,   # stable: sha256(manifest_hash:plan_hash)
            request_id=self.request_id,
            render_plan_ref=self.plan.asset_manifest_ref,
            asset_manifest_ref=self._asset_manifest_ref,
            video_uri=f"file://{output_mp4.resolve()}",
            captions_uri=f"file://{output_srt.resolve()}",
            audio_stems_uri=None,
            hashes=OutputHashes(
                video_sha256=video_hash,
                captions_sha256=captions_hash,
            ),
            provenance=Provenance(
                render_profile=self._profile,
                timing_lock_hash=self.plan.timing_lock_hash,
                rendered_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                ffmpeg_version=ffmpeg_version,
                placeholder_count=self._placeholder_count,
            ),
            lineage=Lineage(
                asset_manifest_hash=manifest_hash,
                render_plan_hash=plan_hash,
            ),
            outputs=[
                OutputArtifact(
                    type="video",
                    path=str(output_mp4.resolve()),
                    sha256=video_hash,
                ),
                OutputArtifact(
                    type="captions",
                    path=str(output_srt.resolve()),
                    sha256=captions_hash,
                ),
            ],
            effective_settings=effective,
            inputs_digest=self._compute_inputs_digest(effective),
            producer=Producer(),
        )

        output_json = self.output_dir / "render_output.json"
        output_json.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        logger.info(
            "Render complete → %s  (placeholders=%d)",
            output_mp4,
            self._placeholder_count,
        )
        return result

    def verify(self) -> RenderFingerprint:
        """
        Validate inputs, run full render, extract frame hashes, write render_fingerprint.json.

        Steps:
          1. Dry-run  — validates inputs; computes stable inputs_digest.
          2. Full render — produces output.mp4 + output.srt (skipped if mp4 already present).
          3. Frame extraction — ffmpeg -f framemd5 on output.mp4.
          4. Write render_fingerprint.json; return RenderFingerprint.

        Raises ValueError if self.dry_run is True.
        """
        if self.dry_run:
            raise ValueError("verify() requires dry_run=False")

        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Step 1: dry-run for input validation + stable inputs_digest
        dry_result = PreviewRenderer(
            self.manifest, self.plan,
            output_dir=self.output_dir,
            asset_manifest_ref=self._asset_manifest_ref,
            dry_run=True,
        ).render()

        # Step 2: full render if output.mp4 not already present
        mp4_path = self.output_dir / "output.mp4"
        if mp4_path.exists():
            ro_path = self.output_dir / "render_output.json"
            full_result = RenderOutput.model_validate_json(
                ro_path.read_text(encoding="utf-8")
            )
        else:
            full_result = self.render()

        # Step 3: per-frame MD5s (deterministic for bit-identical mp4)
        frame_hashes = _extract_frame_hashes(mp4_path)

        # Step 4: build + write fingerprint (no timestamps)
        fp = RenderFingerprint(
            inputs_digest=dry_result.inputs_digest,
            mp4_sha256=full_result.hashes.video_sha256,
            srt_sha256=full_result.hashes.captions_sha256 or "",
            frame_hashes=frame_hashes,
        )
        fp_path = self.output_dir / "render_fingerprint.json"
        fp_path.write_text(fp.model_dump_json(indent=2), encoding="utf-8")
        logger.info("Verify complete → render_fingerprint.json written")
        return fp

    def _dry_run_output(self) -> RenderOutput:
        """
        Build and write render_output.json without executing the render pipeline.

        Called instead of the full render when self.dry_run is True.
        Populates effective_settings so callers can verify schema compatibility
        and inspect intended encoding parameters; outputs[] is left empty because
        no files are produced.
        """
        music_path = _resolve_music(self.manifest)
        _ps = _PROFILE_SETTINGS[self._profile]
        effective = EffectiveSettings(
            resolution=f"{self.plan.resolution.width}x{self.plan.resolution.height}",
            fps=str(self.plan.fps),
            audio_rate="aac" if music_path else "none",
            encoder="libx264",
            crf=_ps["crf"],
            preset=_ps["preset"],
            profile=self._profile,
        )
        result = RenderOutput(
            schema_version="0.0.1",
            schema_id="RenderOutput",
            output_id=self._derived_id,
            request_id=self.request_id,
            # self.plan.asset_manifest_ref carries the render-plan file URI
            # (set by _adapt_plan(); same source used in render() above).
            render_plan_ref=self.plan.asset_manifest_ref,
            asset_manifest_ref=self._asset_manifest_ref,
            video_uri=None,
            captions_uri=None,
            audio_stems_uri=None,
            hashes=OutputHashes(video_sha256="", captions_sha256=None),
            provenance=Provenance(
                render_profile=self._profile,
                timing_lock_hash=self.plan.timing_lock_hash,
                rendered_at="dry-run",
                ffmpeg_version="dry-run",
                placeholder_count=0,
            ),
            lineage=Lineage(
                asset_manifest_hash=self._manifest_hash,
                render_plan_hash=self._plan_hash,
            ),
            outputs=[],
            effective_settings=effective,
            inputs_digest=self._compute_inputs_digest(effective),
            producer=Producer(),
        )
        self.output_dir.mkdir(parents=True, exist_ok=True)
        output_json = self.output_dir / "render_output.json"
        output_json.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        logger.info("Dry-run complete → render_output.json written (no mp4/srt produced)")
        return result

    def _compute_inputs_digest(self, effective: EffectiveSettings) -> str:
        """SHA-256 over canonical JSON of plan + manifest + effective_settings.

        Same canonicalisation as _canonical_json_hash(): sorted keys, compact
        separators, UTF-8.  Order is fixed: plan → manifest → effective_settings.
        """
        h = hashlib.sha256()
        for obj in (self.plan, self.manifest, effective):
            blob = json.dumps(
                obj.model_dump(), sort_keys=True,
                separators=(",", ":"), ensure_ascii=False,
            )
            h.update(blob.encode("utf-8"))
        return h.hexdigest()

    # ------------------------------------------------------------------
    # Internal: asset resolution
    # ------------------------------------------------------------------

    def _resolve_shot_inputs(self, placeholder_dir: Path) -> list[Path]:
        """Return one resolved image Path per shot, in shot-index order."""
        w = self.plan.resolution.width
        h = self.plan.resolution.height
        return [
            self._get_shot_visual(shot, placeholder_dir, w, h)
            for shot in self.manifest.shots
        ]

    def _get_shot_visual(
        self,
        shot: Shot,
        placeholder_dir: Path,
        w: int,
        h: int,
    ) -> Path:
        """
        Return the best available visual Path for a shot.

        Priority:
          1. plan.asset_resolutions[asset_id] (asset resolver output)
          2. asset.asset_uri on the AssetManifest itself
        Backgrounds are preferred over characters / props (sorted by role).
        Falls back to a generated placeholder if no usable file is found.
        """
        candidates = sorted(
            shot.visual_assets,
            key=lambda a: (0 if a.role == "background" else 1),
        )
        for asset in candidates:
            uri = self.plan.asset_resolutions.get(asset.asset_id) or asset.asset_uri
            if not uri:
                continue
            path = _resolve_uri(uri)
            if path and path.exists():
                return path

        # No usable asset → synthesise placeholder.
        self._placeholder_count += 1
        logger.debug(
            "No visual asset found for shot %r — generating placeholder.", shot.shot_id
        )
        fb = self.plan.fallback
        font_path: Optional[str] = None
        if Path(fb.placeholder_font_path).exists():
            font_path = fb.placeholder_font_path

        return generate_placeholder(
            shot_id=shot.shot_id,
            width=w,
            height=h,
            color=fb.placeholder_color,
            font_path=font_path,
            font_size=fb.placeholder_font_size,
            cache_dir=placeholder_dir,
        )

    # ------------------------------------------------------------------
    # Internal: ffmpeg
    # ------------------------------------------------------------------

    def _run_concat(self, shot_inputs: list[Path], output_path: Path) -> None:
        """
        Build and execute a deterministic ffmpeg concat command.

        Each shot becomes one `-loop 1 -framerate N -t dur -i file` input.
        A filter_complex scales/pads each input to the target resolution then
        concatenates with `concat=n=N:v=1:a=0`.

        Determinism flags applied:
          -fflags +bitexact        suppress non-deterministic metadata writes
          -flags:v +bitexact       deterministic video encoder path
          -map_metadata -1         strip creation_time and encoder strings
          -movflags +faststart     consistent MP4 atom ordering
          (libx264 is deterministic for fixed crf/preset/pix_fmt/fps)
        """
        w = self.plan.resolution.width
        h = self.plan.resolution.height
        fps = self.plan.fps
        n = len(shot_inputs)

        cmd: list[str] = ["ffmpeg", "-y"]

        # --- Video inputs ---
        for shot, path in zip(self.manifest.shots, shot_inputs):
            dur_s = shot.duration_ms / 1000.0
            cmd += [
                "-loop", "1",
                "-framerate", str(fps),
                "-t", f"{dur_s:.6f}",
                "-i", str(path),
            ]

        # --- Optional music input (index = n) ---
        music_path = _resolve_music(self.manifest)
        if music_path is not None:
            cmd += ["-i", str(music_path)]

        # --- filter_complex: scale + pad each video input, then concat ---
        total_dur_s = sum(s.duration_ms for s in self.manifest.shots) / 1000.0
        filter_parts: list[str] = []
        for i in range(n):
            filter_parts.append(
                f"[{i}:v]"
                f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,"
                f"setsar=1,"
                f"fps={fps}"
                f"[v{i}]"
            )
        concat_in = "".join(f"[v{i}]" for i in range(n))
        filter_parts.append(f"{concat_in}concat=n={n}:v=1:a=0[vout]")

        cmd += ["-filter_complex", ";".join(filter_parts)]
        cmd += ["-map", "[vout]"]

        # --- Audio ---
        # Phase-0 constant: aac is the only supported audio codec.
        # Configurable codec support is deferred to Phase 1.
        if music_path is not None:
            cmd += [
                "-map", f"{n}:a",
                "-t", f"{total_dur_s:.6f}",
                "-c:a", "aac",          # Phase-0 constant
                "-flags:a", "+bitexact",
            ]
        else:
            cmd += ["-an"]

        # --- Encoding constants + determinism flags ---
        # CRF and preset come from the profile registry (_PROFILE_SETTINGS).
        # pix_fmt is fixed for broadest decoder compatibility.
        # Determinism flags ensure bit-identical output for the same inputs:
        #   -fflags +bitexact / -flags:v +bitexact — suppress non-reproducible metadata
        #   -map_metadata -1                        — strip creation_time, encoder strings
        #   -movflags +faststart                    — consistent MP4 atom ordering
        _ps = _PROFILE_SETTINGS[self._profile]
        cmd += [
            "-c:v", "libx264",
            "-crf", _ps["crf"],
            "-preset", _ps["preset"],
            "-pix_fmt", "yuv420p",  # Phase-0 constant
            "-r", str(fps),
            "-fflags", "+bitexact",
            "-flags:v", "+bitexact",
            "-map_metadata", "-1",
            "-movflags", "+faststart",
            str(output_path),
        ]

        run_ffmpeg(cmd)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _resolve_uri(uri: str) -> Optional[Path]:
    """Resolve a file:// URI or plain filesystem path to a Path object."""
    if not uri:
        return None
    if uri.startswith("file://"):
        return Path(urlparse(uri).path)
    return Path(uri)


def _resolve_music(manifest: AssetManifest) -> Optional[Path]:
    """Return the resolved music track Path, or None if absent / unreadable."""
    if not manifest.music_uri:
        return None
    path = _resolve_uri(manifest.music_uri)
    if path and path.exists():
        return path
    logger.warning(
        "music_uri %r not found — rendering without background music.",
        manifest.music_uri,
    )
    return None


def _canonical_json_hash(obj: dict) -> str:
    """SHA-256 of canonical JSON — sorted keys, compact separators, UTF-8.

    Used for lineage hashes and deterministic ID derivation so the same
    Pydantic model always produces the same hash regardless of dict insertion
    order or serialisation library internals (e.g. model_dump_json() key order
    is not guaranteed to be stable across Pydantic versions).
    """
    canonical = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65_536), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _extract_frame_hashes(mp4_path: Path) -> list[str]:
    """Extract per-frame MD5 lines via ffmpeg -f framemd5; strips comment lines."""
    result = subprocess.run(
        ["ffmpeg", "-i", str(mp4_path), "-f", "framemd5", "-"],
        capture_output=True, text=True, check=True,
    )
    return [ln for ln in result.stdout.splitlines() if not ln.startswith("#")]
