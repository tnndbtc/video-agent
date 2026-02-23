#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
ASSETS_DIR="${VIDEO_TEST_ASSETS:-${REPO_ROOT}/.cache/video_test_assets}"

echo "=== Integration Test Runner: render_real ==="
echo "Assets dir : ${ASSETS_DIR}"
echo ""

mkdir -p "${ASSETS_DIR}"

echo "--- Generating test assets (idempotent) ---"
python "${REPO_ROOT}/scripts/generate_test_media.py" --out "${ASSETS_DIR}"

export VIDEO_TEST_ASSETS="${ASSETS_DIR}"

echo ""
echo "--- Running integration tests ---"
cd "${REPO_ROOT}/worker"
pytest -rs -q -k "render_real" tests/integration/test_render_real.py -v

echo ""
echo "=== Done ==="
echo "VIDEO_TEST_ASSETS : ${ASSETS_DIR}"
echo ""
echo "Re-run quickly:"
echo "  export VIDEO_TEST_ASSETS=${ASSETS_DIR}"
echo "  cd worker && pytest -rs -q -k 'render_real' tests/integration/test_render_real.py -v"
