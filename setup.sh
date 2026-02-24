#!/bin/bash

# =============================================================================
# Video Agent Setup Script
# =============================================================================
# Interactive script for running tests and installing requirements
# =============================================================================

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color
BOLD='\033[1m'

# Project directory (where this script is located)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# =============================================================================
# Helper Functions
# =============================================================================

print_header() {
    echo ""
    echo -e "${CYAN}========================================${NC}"
    echo -e "${CYAN}  $1${NC}"
    echo -e "${CYAN}========================================${NC}"
    echo ""
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

# Check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Resolve the Python interpreter and pip for this project.
# Priority:
#   1. Already-activated virtualenv  ($VIRTUAL_ENV)
#   2. Local .venv in project root   ($SCRIPT_DIR/.venv)
#   3. virtualenvwrapper venv named  (~/.virtualenvs/<project-dir-name>)
#   4. System python3 / pip3
resolve_python_cmd() {
    local project_name
    project_name="$(basename "$SCRIPT_DIR")"

    if [[ -n "${VIRTUAL_ENV:-}" ]]; then
        echo "$VIRTUAL_ENV/bin/python"
        return
    fi

    if [[ -x "$SCRIPT_DIR/.venv/bin/python" ]]; then
        echo "$SCRIPT_DIR/.venv/bin/python"
        return
    fi

    if [[ -x "$HOME/.virtualenvs/$project_name/bin/python" ]]; then
        echo "$HOME/.virtualenvs/$project_name/bin/python"
        return
    fi

    echo "python3"
}

resolve_pip_cmd() {
    local py
    py="$(resolve_python_cmd)"
    # Derive pip from the same interpreter so they always match
    local pip_path="${py%python*}pip"
    if [[ -x "$pip_path" ]]; then
        echo "$pip_path"
    else
        # Fallback: ask the interpreter itself for pip
        echo "$py -m pip"
    fi
}

# =============================================================================
# Requirements Installation
# =============================================================================

install_requirements() {
    print_header "Installing Requirements"

    local pip_cmd
    pip_cmd="$(resolve_pip_cmd)"
    print_info "Using pip: $pip_cmd"

    # ── Python packages ───────────────────────────────────────────────────────
    local req_file="$SCRIPT_DIR/tools/requirements.txt"
    if [[ -f "$req_file" ]]; then
        print_info "Installing Python packages from tools/requirements.txt ..."
        if $pip_cmd install -r "$req_file"; then
            print_success "Python packages installed"
        else
            print_warning "pip install failed — check that pip/virtualenv is available"
        fi
    else
        print_info "No tools/requirements.txt found — skipping Python packages"
    fi

    # ── Project package (pyproject.toml) ──────────────────────────────────────
    local pyproject_file="$SCRIPT_DIR/pyproject.toml"
    if [[ -f "$pyproject_file" ]]; then
        print_info "Installing project package in editable mode (pip install -e .) ..."
        if $pip_cmd install -e "$SCRIPT_DIR"; then
            print_success "Project package installed"
        else
            print_warning "Project package install failed — check pyproject.toml"
        fi
    else
        print_info "No pyproject.toml found — skipping project package install"
    fi

    echo ""
    print_success "Requirements installed!"
    echo ""
}

# =============================================================================
# Test Runner
# =============================================================================

run_tests() {
    print_header "Run Tests"

    local python_cmd
    python_cmd="$(resolve_python_cmd)"
    print_info "Using Python: $python_cmd"

    print_info "Slow tests auto-skip if ffmpeg is not on PATH."
    local _rc=0

    echo ""
    print_info "── 1/3  Renderer tests (unit + golden + integration) ─────────────"
    if $python_cmd -m pytest -q --tb=short \
        --ignore=tools/tests/integration/test_e2e_render_pipeline.py; then
        print_success "Renderer tests PASSED"
    else
        print_warning "Renderer tests FAILED"
        _rc=1
    fi

    echo ""
    print_info "── 2/3  Contracts verifier ───────────────────────────────────────"
    if $python_cmd third_party/contracts/tools/verify_contracts.py; then
        print_success "Contracts verifier PASSED"
    else
        print_warning "Contracts verifier FAILED"
        _rc=1
    fi

    echo ""
    print_info "── 3/3  E2E render pipeline (final-format manifest + orchestrator plan)"
    print_info "        Output → /tmp/video-agent-e2e-<timestamp>/  (deleted after run)"
    if $python_cmd -m pytest \
        tools/tests/integration/test_e2e_render_pipeline.py \
        -v -s --tb=short; then
        print_success "E2E render pipeline PASSED"
    else
        print_warning "E2E render pipeline FAILED"
        _rc=1
    fi

    echo ""
    if [[ $_rc -eq 0 ]]; then
        print_success "ALL non-container tests PASSED"
    else
        print_warning "One or more non-container tests FAILED (see output above)"
    fi
}

# =============================================================================
# Usage
# =============================================================================

show_usage() {
    print_header "Usage — Render Video from JSON Inputs"

    echo -e "${BOLD}Inputs:${NC}  AssetManifest.final.json  +  RenderPlan.json"
    echo -e "${BOLD}Outputs:${NC} output.mp4, output.srt, RenderOutput.json"
    echo ""

    echo -e "${CYAN}── Canonical render  (video render — §41.4) ─────────────────────────${NC}"
    echo ""
    echo -e "  video render \\"
    echo -e "      --manifest /path/to/AssetManifest.final.json \\"
    echo -e "      --plan     /path/to/RenderPlan.json \\"
    echo -e "      --out      /tmp/out/RenderOutput.json \\"
    echo -e "      --video    /tmp/out/output.mp4"
    echo ""
    echo -e "  ${YELLOW}Stdout:${NC} full RenderOutput JSON"
    echo -e "  ${YELLOW}Stderr:${NC} error message on failure (exit code 1)"
    echo -e "  ${YELLOW}--srt${NC}   optional; defaults to <video path>.srt"
    echo ""

    echo -e "${CYAN}── Dry run (validate + write RenderOutput only, no mp4/srt) ─────────${NC}"
    echo ""
    echo -e "  video render \\"
    echo -e "      --manifest /path/to/AssetManifest.final.json \\"
    echo -e "      --plan     /path/to/RenderPlan.json \\"
    echo -e "      --out      /tmp/out/RenderOutput.json \\"
    echo -e "      --video    /tmp/out/output.mp4 \\"
    echo -e "      --dry-run"
    echo ""

    echo -e "${CYAN}── Legacy wrapper  (--out-dir interface) ────────────────────────────${NC}"
    echo ""
    echo -e "  python scripts/render_from_orchestrator.py \\"
    echo -e "      --asset-manifest /path/to/AssetManifest.json \\"
    echo -e "      --render-plan    /path/to/RenderPlan.json \\"
    echo -e "      --out-dir        /tmp/out"
    echo ""
    echo -e "  ${YELLOW}Note:${NC} delegates to 'video render' internally."
    echo -e "         Also supports ${BOLD}--verify${NC} (emits render_fingerprint.json)."
    echo ""

    echo -e "${CYAN}── Accepted manifest formats ────────────────────────────────────────${NC}"
    echo ""
    echo -e "  ${BOLD}Native Pydantic${NC}     top-level \"shots\" key"
    echo -e "  ${BOLD}Orchestrator draft${NC}  \"backgrounds\" / \"character_packs\" / \"vo_items\" keys"
    echo -e "  ${BOLD}Final / media${NC}       flat \"items\" list  (AssetManifest_final / AssetManifest.media)"
    echo ""
}

# =============================================================================
# Main Menu
# =============================================================================

show_menu() {
    echo ""
    echo -e "${CYAN}============================================${NC}"
    echo -e "${CYAN}       Video Agent Setup Menu${NC}"
    echo -e "${CYAN}============================================${NC}"
    echo ""
    echo -e "  ${BOLD}1)${NC} Run tests"
    echo -e "     ${GREEN}(Non-container local test suites)${NC}"
    echo ""
    echo -e "  ${BOLD}2)${NC} Install requirements"
    echo -e "     ${CYAN}(Install tools/requirements.txt + project package)${NC}"
    echo ""
    echo -e "  ${BOLD}3)${NC} Show usage"
    echo -e "     ${YELLOW}(How to run the renderer CLI)${NC}"
    echo ""
    echo -e "  ${BOLD}0)${NC} Exit"
    echo ""
    echo -e "${CYAN}============================================${NC}"
}

# =============================================================================
# Main Script
# =============================================================================

main() {
    clear
    echo ""
    echo -e "${CYAN}  Video Agent${NC}"
    echo ""

    while true; do
        show_menu
        read -p "Enter your choice [0-3]: " choice

        case $choice in
            1)
                run_tests
                ;;
            2)
                install_requirements
                ;;
            3)
                show_usage
                ;;
            0)
                echo ""
                print_info "Goodbye!"
                echo ""
                exit 0
                ;;
            *)
                print_warning "Invalid option. Please enter 0, 1, 2, or 3."
                ;;
        esac

        echo ""
        read -p "Press Enter to continue..."
    done
}

# Run main function
main "$@"
