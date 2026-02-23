# video/tools — Phase 0 Pipeline Renderer (Workstream C)

Standalone tools implementing §19.0 Workstream C of the master pipeline plan.

**Inputs:**  `AssetManifest.json` (§5.7) + `RenderPlan.json` (§5.8, `profile: preview_local`)
**Outputs:** `output.mp4` · `output.srt` · `render_output.json` (§5.9)

---

## Structure

```
video/tools/
├── schemas/            Pydantic v2 models for §5.7, §5.8, §5.9
│   ├── asset_manifest.py
│   ├── render_plan.py
│   └── render_output.py
├── renderer/           Phase 0 renderer (local-only, no worker imports)
│   ├── ffmpeg_runner.py    standalone ffmpeg subprocess helper
│   ├── placeholder.py      Pillow-based placeholder PNG generator
│   ├── captions.py         SRT generator from VO lines
│   └── preview_local.py    PreviewRenderer entry point
└── tests/
    ├── conftest.py          shared fixtures (deterministic test assets)
    ├── unit/
    │   ├── test_schemas.py
    │   └── test_placeholder.py
    └── golden/
        ├── test_preview_golden.py    framemd5 + schema validation tests
        ├── generate_golden.py        regenerate expected/*.framemd5
        └── expected/
            └── render_preview_5shots.framemd5   committed golden hashes
```

---

## Requirements

### Python

- Python ≥ 3.11
- Pydantic v2 (`pydantic ^2.5`)
- Pillow ≥ 10 (`Pillow ^10.2`)

These are already present in `video/worker/pyproject.toml`. No new
`pyproject.toml` is needed for Phase 0 local usage.

Install if running outside the worker virtualenv:

```bash
pip install "pydantic>=2.5" "Pillow>=10"
```

### ffmpeg (for render tests)

**Required version: 6.1.x**
Tested on `ffmpeg 6.1.1-3ubuntu5` (Ubuntu 24.04, libx264).

The golden render test (`tests/golden/expected/render_preview_5shots.framemd5`)
stores per-frame MD5 hashes produced by this exact version. A different ffmpeg
**major.minor** version may produce a different H.264 bitstream even with
identical input and flags, causing the golden comparison to fail.

To check your installed version:

```bash
ffmpeg -version | head -1
# ffmpeg version 6.1.1-3ubuntu5 ...
```

To install on Ubuntu:

```bash
sudo apt install ffmpeg          # Ubuntu 24.04 ships 6.1.x
```

If you intentionally upgrade ffmpeg and want to update the golden hashes:

```bash
cd video/tools
python tests/golden/generate_golden.py
git add tests/golden/expected/render_preview_5shots.framemd5
git commit -m "update golden render hashes for ffmpeg X.Y"
```

### System font (for placeholder text)

Default: `/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf` (present on Ubuntu).

Override via `RenderPlan.fallback.placeholder_font_path` if using a different host.
The renderer falls back to Pillow's built-in font if the path is missing (no error).

---

## Usage

### Python API

```python
import sys
sys.path.insert(0, "/path/to/video/tools")   # or set PYTHONPATH

from pathlib import Path
from schemas.asset_manifest import AssetManifest
from schemas.render_plan import RenderPlan
from renderer.preview_local import PreviewRenderer

manifest = AssetManifest.model_validate_json(
    Path("asset_manifest.json").read_text()
)
plan = RenderPlan.model_validate_json(
    Path("render_plan.json").read_text()
)

result = PreviewRenderer(manifest, plan, output_dir=Path("/tmp/out")).render()
print(result.model_dump_json(indent=2))
# → writes /tmp/out/output.mp4, output.srt, render_output.json
```

### CLI smoke test

```bash
cd video/tools
PYTHONPATH=. python -c "
from pathlib import Path
from schemas.asset_manifest import AssetManifest, Shot, VisualAsset
from schemas.render_plan import RenderPlan
from renderer.preview_local import PreviewRenderer

manifest = AssetManifest(
    manifest_id='smoke', project_id='p',
    shotlist_ref='file:///sl.json',
    timing_lock_hash='sha256:smoke',
    shots=[Shot(shot_id='s1', duration_ms=2000)]
)
plan = RenderPlan(
    plan_id='p1', project_id='p',
    asset_manifest_ref='file:///m.json',
    timing_lock_hash='sha256:smoke'
)
r = PreviewRenderer(manifest, plan, output_dir=Path('/tmp/smoke')).render()
print('video:', r.video_uri)
print('placeholder_count:', r.provenance.placeholder_count)
"
```

---

## Tests

```bash
cd video/tools

# Unit tests (no ffmpeg needed)
PYTHONPATH=. pytest tests/unit/ -v

# Golden render tests (requires ffmpeg 6.1.x)
# First run: generate expected hashes
python tests/golden/generate_golden.py

# Then run
PYTHONPATH=. pytest tests/golden/ -v -m slow

# All tests
PYTHONPATH=. pytest tests/ -v
```

---

## Phase 0 Constraints (explicit out-of-scope)

The following are intentionally deferred and NOT implemented:

| Feature | Phase |
|---|---|
| Ken Burns / zoompan motion effects | Phase 1 |
| Crossfade / xfade transitions | Phase 1 |
| TTS audio generation | Phase 1 |
| Burned-in subtitle captions | Phase 1 |
| Background music loudness ducking | Phase 1 |
| HQ / standard_local render profiles | Phase 4 |
| Distributed queue workers | Phase 5 |
| Multi-tenancy isolation | Phase 2 |
| Full rights gate enforcement | Phase 3 |
| Content safety scanning | Phase 2 |

---

## Schema Ownership (§38.1)

`RenderOutput` and rendering templates are owned by `video / video-engine` (this repo).
Do not alter `AssetManifest` or `RenderPlan` required fields without a cross-team
change request per §38.6.
