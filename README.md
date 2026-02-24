# video-agent

Phase 0 deterministic video renderer. Accepts an **AssetManifest** and a **RenderPlan** as JSON inputs and produces an MP4 video with SRT captions. Identical inputs always produce bit-identical outputs.

## What it does

- Composes still images into a shot-based video timeline (cut transitions only)
- Generates SRT subtitle captions from voice-over script data
- Auto-generates placeholder images (via Pillow) for any missing assets
- Mixes optional background music (AAC)
- Emits a `render_output.json` with SHA-256 hashes, lineage, and provenance for every render

**Phase 0 scope:** static images, cut transitions, SRT captions, optional music. No motion effects, no TTS, no burned-in subs — those are Phase 1+.

---

## Requirements

| Dependency | Version |
|---|---|
| Python | ≥ 3.11 |
| Pydantic | ≥ 2.5 |
| Pillow | ≥ 10 |
| FFmpeg | 6.1.x (exact version needed for golden-test determinism) |

Ubuntu 24.04 ships FFmpeg 6.1.1-3ubuntu5 (recommended).

---

## Install

```bash
# Create and activate a virtualenv (virtualenvwrapper shown, plain venv works too)
mkvirtualenv video-agent
workon video-agent

# Install all dependencies + the project package
bash setup.sh   # choose option 2 — Install requirements
# or manually:
pip install -r tools/requirements.txt
pip install -e .
```

---

## CLI reference

The package exposes one entry point after `pip install -e .`:

```
video <subcommand> [options]
```

### `video verify` — system verification

Renders a built-in canary scene twice and checks for determinism.

```bash
video verify [--strict] [--profile {preview|high}]
```

| Flag | Default | Description |
|---|---|---|
| `--strict` | off | Exit 1 on `mp4_sha256` mismatch instead of warning |
| `--profile` | `preview` | Quality profile: `preview` (CRF 28) or `high` (CRF 18) |

Prints a JSON `RenderAudit` to stdout. Exit 0 on pass, 1 on failure.

---

### `video audit-render` — nondeterminism detector

Renders the same inputs twice and diffs the outputs.

```bash
video audit-render <render_plan> <asset_manifest> [--dry-run]
```

| Argument | Description |
|---|---|
| `render_plan` | Path to RenderPlan JSON |
| `asset_manifest` | Path to AssetManifest JSON |
| `--dry-run` | Compare dry-run outputs only (no ffmpeg call; faster) |

Prints a JSON `RenderAudit` with a `diff_fields` list. Exit 0 if identical, 1 if diffs found.

---

### `scripts/render_from_orchestrator.py` — standalone renderer

Format-agnostic renderer script. Auto-detects native or orchestrator manifest formats.

```bash
python scripts/render_from_orchestrator.py \
    --asset-manifest /path/to/AssetManifest.json \
    --render-plan    /path/to/RenderPlan.json \
    --out-dir        /tmp/out
```

| Flag | Required | Description |
|---|---|---|
| `--asset-manifest` | yes | Path to AssetManifest JSON |
| `--render-plan` | yes | Path to RenderPlan JSON |
| `--out-dir` | yes | Output directory |
| `--dry-run` | no | Validate inputs only; write `render_output.json` but skip mp4/srt |
| `--verify` | no | Dry-run + full render; emit `render_fingerprint.json`. Mutually exclusive with `--dry-run` |

**Stdout:** `RenderOutput` JSON
**Stderr:** Error message on failure
**Exit codes:** 0 success, 1 failure

**Accepted manifest formats (auto-detected):**

| Format | Detection key |
|---|---|
| Native Pydantic | top-level `"shots"` key |
| Orchestrator draft | `"backgrounds"` / `"character_packs"` / `"vo_items"` keys |
| Final / media | flat `"items"` list |

---

## Outputs

| File | When produced | Description |
|---|---|---|
| `output.mp4` | always (not `--dry-run`) | H.264/AAC video |
| `output.srt` | always (not `--dry-run`) | SubRip captions |
| `render_output.json` | always | Hashes, lineage, provenance, effective settings |
| `render_fingerprint.json` | `--verify` only | Timestamp-free fingerprint + per-frame MD5s |

---

## Running tests

```bash
# All tests (slow tests auto-skip if ffmpeg is absent)
pytest -q --tb=short

# Unit tests only (no ffmpeg required)
pytest tools/tests/unit/ -v

# Integration tests
pytest tools/tests/integration/ -v

# Golden render tests (requires ffmpeg 6.1.x)
pytest tools/tests/golden/ -v -m slow
```

The `setup.sh` option 1 runs all non-container test suites (pytest + contracts verifier).

---

## Project layout

```
video-agent/
├── tools/
│   ├── cli.py                  # `video` entry point (verify, audit-render)
│   ├── schemas/                # Pydantic models for all JSON contracts
│   │   ├── asset_manifest.py   # AssetManifest, Shot, VOLine, VisualAsset
│   │   ├── render_plan.py      # RenderPlan, Resolution, FallbackConfig
│   │   └── render_output.py    # RenderOutput, RenderFingerprint, RenderAudit
│   ├── renderer/               # Rendering pipeline
│   │   ├── preview_local.py    # PreviewRenderer — main orchestrator
│   │   ├── ffmpeg_runner.py    # FFmpeg subprocess wrapper
│   │   ├── captions.py         # SRT generator from VO lines
│   │   └── placeholder.py      # Pillow placeholder PNG generator
│   └── tests/                  # unit / golden / integration
├── scripts/
│   ├── render_from_orchestrator.py  # Format-agnostic renderer CLI
│   └── generate_test_media.py       # Create test JPG/MP4/MP3 fixtures
├── third_party/contracts/      # Pinned JSON schemas + contracts verifier
├── pyproject.toml              # Package metadata + `video` entry point
├── setup.sh                    # Interactive setup / test runner
└── PROTOCOL_VERSION            # Pinned contracts version (1.0.2)
```
