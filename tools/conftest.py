"""
Root conftest for video/tools/.

Adds video/tools/ to sys.path so that `schemas`, `renderer`, and
other top-level packages are importable without installing a package.

This file is picked up automatically by pytest when tests under
video/tools/tests/ are collected.
"""
import sys
from pathlib import Path

_TOOLS_ROOT = Path(__file__).parent
if str(_TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(_TOOLS_ROOT))

# Make verify_contracts (and the rest of contracts/tools/) importable from
# every test under tools/tests/ so contract schema checks can be written
# without per-file sys.path boilerplate.
_CONTRACTS_TOOLS = Path(__file__).parent.parent / "third_party" / "contracts" / "tools"
if str(_CONTRACTS_TOOLS) not in sys.path:
    sys.path.insert(0, str(_CONTRACTS_TOOLS))

# Shared path constants used by contract-validation tests.
CONTRACTS_SCHEMAS_DIR = Path(__file__).parent.parent / "third_party" / "contracts" / "schemas"
CONTRACTS_GOLDENS_DIR = Path(__file__).parent.parent / "third_party" / "contracts" / "goldens"
