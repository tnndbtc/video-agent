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

# =============================================================================
# Requirements Installation
# =============================================================================

install_requirements() {
    print_header "Installing Requirements"

    # ── Python packages ───────────────────────────────────────────────────────
    local req_file="$SCRIPT_DIR/tools/requirements.txt"
    if [[ -f "$req_file" ]]; then
        print_info "Installing Python packages from tools/requirements.txt ..."
        local pip_cmd
        if [[ -n "${VIRTUAL_ENV:-}" ]]; then
            pip_cmd="$VIRTUAL_ENV/bin/pip"
        else
            pip_cmd="pip3"
        fi
        if $pip_cmd install -r "$req_file"; then
            print_success "Python packages installed"
        else
            print_warning "pip install failed — check that pip/virtualenv is available"
        fi
    else
        print_info "No tools/requirements.txt found — skipping Python packages"
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

    # Python interpreter — respects active virtualenv
    local python_cmd
    if [[ -n "${VIRTUAL_ENV:-}" ]]; then
        python_cmd="$VIRTUAL_ENV/bin/python"
    else
        python_cmd="python3"
    fi

    print_info "Slow tests auto-skip if ffmpeg is not on PATH."
    local _rc=0

    echo ""
    print_info "── 1/2  Renderer tests (unit + golden + integration) ─────────────"
    if $python_cmd -m pytest -q --tb=short; then
        print_success "Renderer tests PASSED"
    else
        print_warning "Renderer tests FAILED"
        _rc=1
    fi

    echo ""
    print_info "── 2/2  Contracts verifier ───────────────────────────────────────"
    if $python_cmd third_party/contracts/tools/verify_contracts.py; then
        print_success "Contracts verifier PASSED"
    else
        print_warning "Contracts verifier FAILED"
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

    echo -e "${BOLD}Script:${NC}  scripts/render_from_orchestrator.py"
    echo -e "${BOLD}Inputs:${NC}  AssetManifest.json  +  RenderPlan.json"
    echo -e "${BOLD}Outputs:${NC} output.mp4, output.srt, render_output.json"
    echo ""

    echo -e "${CYAN}── Standard render ──────────────────────────────────────────────────${NC}"
    echo ""
    echo -e "  python scripts/render_from_orchestrator.py \\"
    echo -e "      --asset-manifest /path/to/AssetManifest.json \\"
    echo -e "      --render-plan    /path/to/RenderPlan.json \\"
    echo -e "      --out-dir        /tmp/out"
    echo ""
    echo -e "  ${YELLOW}Stdout:${NC} full RenderOutput JSON"
    echo -e "  ${YELLOW}Stderr:${NC} error message on failure (exit code 1)"
    echo ""

    echo -e "${CYAN}── Dry run (validate inputs only, no mp4/srt produced) ──────────────${NC}"
    echo ""
    echo -e "  python scripts/render_from_orchestrator.py \\"
    echo -e "      --asset-manifest /path/to/AssetManifest.json \\"
    echo -e "      --render-plan    /path/to/RenderPlan.json \\"
    echo -e "      --out-dir        /tmp/out \\"
    echo -e "      --dry-run"
    echo ""

    echo -e "${CYAN}── Verify (dry-run + full render, emits render_fingerprint.json) ─────${NC}"
    echo ""
    echo -e "  python scripts/render_from_orchestrator.py \\"
    echo -e "      --asset-manifest /path/to/AssetManifest.json \\"
    echo -e "      --render-plan    /path/to/RenderPlan.json \\"
    echo -e "      --out-dir        /tmp/out \\"
    echo -e "      --verify"
    echo ""
    echo -e "  ${YELLOW}Note:${NC} --verify and --dry-run are mutually exclusive"
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
    echo -e "     ${CYAN}(Install Python packages from tools/requirements.txt)${NC}"
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
