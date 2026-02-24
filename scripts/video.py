#!/usr/bin/env python3
"""
video — renderer CLI entry point (scripts/ shim).

All logic lives in tools/cli.py.  This script provides a stable invocation
path for tests and standalone use without requiring `pip install -e .`.

Re-exports cmd_verify, cmd_render, cmd_audit_render, and _fingerprint_bytes
at module level so that tests which load this file directly via importlib can
access those symbols (see test_video_cli.py).
"""
from __future__ import annotations

import sys
from pathlib import Path

_TOOLS_ROOT = Path(__file__).resolve().parents[1] / "tools"
if str(_TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(_TOOLS_ROOT))

from cli import (  # noqa: E402, F401
    main,
    cmd_render,
    cmd_verify,
    cmd_audit_render,
    _fingerprint_bytes,
)

if __name__ == "__main__":
    main()
