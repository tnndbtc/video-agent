#!/usr/bin/env python3
"""Contract verification tool for orchestrator schemas and goldens.

Standalone script; also importable for tests.

Usage:
    python contracts/tools/verify_contracts.py [--contracts-dir PATH]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import jsonschema

# Default contracts dir is parent of this file's parent (i.e., contracts/)
CONTRACTS_DIR = Path(__file__).parent.parent

# Filename stem → schema filename mapping
SCHEMA_MAP: dict[str, str] = {
    "Script": "Script.v1.json",
    "ShotList": "ShotList.v1.json",
    "AssetManifest_draft": "AssetManifest.v1.json",
    "AssetManifest.media": "AssetManifest.media.v1.json",
    "AssetManifest_final": "AssetManifest_final.v1.json",
    "RenderPlan": "RenderPlan.v1.json",
    "RenderOutput": "RenderOutput.v1.json",
    "RenderPackage": "RenderPackage.v1.json",
    "EpisodeBundle": "EpisodeBundle.v1.json",
}

_RE_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
_EPOCH_SENTINEL = "1970-01-01T00:00:00Z"
_RE_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
# Explicitly allow file:///placeholder and file:///placeholder/... only.
# Any other file:// URI (e.g. file:///tmp, file:///home, file:///prod) is flagged.
_RE_FILE_URI_ALLOWED = re.compile(r"^file:///placeholder(/|$)")
_RE_FILE_URI = re.compile(r"^file:///")
_RE_ABS_PATH = re.compile(r"^/[a-z]|^[A-Z]:\\")


def canonical_bytes(data: object) -> bytes:
    """Return json.dumps(sort_keys=True, separators=(',',':')) + b'\\n'."""
    return (json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def check_canonical(raw_bytes: bytes, rel_path: str) -> list[str]:
    """Compare raw_bytes against canonical_bytes(json.loads(raw_bytes)).

    Returns a list of error strings (empty if the file is canonical).
    """
    try:
        data = json.loads(raw_bytes)
    except json.JSONDecodeError as exc:
        return [f"NOT_CANONICAL: JSON parse error in {rel_path}: {exc}"]
    expected = canonical_bytes(data)
    if raw_bytes != expected:
        return [f"NOT_CANONICAL: {rel_path}"]
    return []


def check_schema(data: dict, golden_name: str, schemas_dir: Path) -> list[str]:
    """Validate data against the schema mapped from golden_name.

    Returns a list of error strings (empty if valid or no schema mapping).
    """
    stem = Path(golden_name).stem  # e.g. "Script" from "Script.json"
    schema_file = SCHEMA_MAP.get(stem)
    if schema_file is None:
        return []  # No mapping; skip unknown goldens

    schema_path = schemas_dir / schema_file
    if not schema_path.exists():
        return [f"SCHEMA_INVALID: {golden_name}: schema file not found: {schema_path}"]

    try:
        schema = json.loads(schema_path.read_bytes())
        validator = jsonschema.Draft7Validator(schema)
        errs = list(validator.iter_errors(data))
        if errs:
            msgs = "; ".join(e.message for e in errs[:3])
            return [f"SCHEMA_INVALID: {golden_name}: {msgs}"]
    except Exception as exc:  # noqa: BLE001
        return [f"SCHEMA_INVALID: {golden_name}: {exc}"]

    return []


def _check_string_value(
    value: str,
    key: str,
    rel_path: str,
    errors: list[str],
) -> None:
    """Append an error if *value* is non-deterministic."""
    if _RE_DATETIME.match(value) and value != _EPOCH_SENTINEL:
        errors.append(
            f"NON_DETERMINISTIC: {rel_path}: field '{key}' contains datetime: {value!r}"
        )
    elif _RE_UUID.match(value):
        errors.append(
            f"NON_DETERMINISTIC: {rel_path}: field '{key}' contains UUID: {value!r}"
        )
    elif _RE_FILE_URI.match(value) and not _RE_FILE_URI_ALLOWED.match(value):
        errors.append(
            f"NON_DETERMINISTIC: {rel_path}: field '{key}' contains file URI: {value!r}"
        )
    elif _RE_ABS_PATH.match(value):
        errors.append(
            f"NON_DETERMINISTIC: {rel_path}: field '{key}' contains absolute path:"
            f" {value!r}"
        )


def _walk_values(
    data: object,
    current_key: str,
    allowlist_schema: dict,
    errors: list[str],
    rel_path: str,
) -> None:
    """Recursively walk all string values in *data*."""
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, str):
                if k not in allowlist_schema:
                    _check_string_value(v, k, rel_path, errors)
            elif isinstance(v, (dict, list)):
                _walk_values(v, k, allowlist_schema, errors, rel_path)
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                if current_key not in allowlist_schema:
                    _check_string_value(item, current_key, rel_path, errors)
            elif isinstance(item, (dict, list)):
                _walk_values(item, current_key, allowlist_schema, errors, rel_path)


def check_determinism(data: dict, golden_name: str, allowlist: dict) -> list[str]:
    """Walk all string values; flag ISO datetimes (not epoch), UUIDs, real abs paths.

    Returns a list of error strings (empty if all values are deterministic).
    """
    stem = Path(golden_name).stem  # e.g. "ShotList" from "ShotList.json"
    allowlist_schema: dict = allowlist.get(stem, {})
    errors: list[str] = []
    _walk_values(data, "", allowlist_schema, errors, golden_name)
    return errors


def run_checks(contracts_dir: Path) -> tuple[list[str], int]:
    """Discover all goldens/**/*.json, run all 3 checks, return (errors, golden_count).

    Also prints per-golden PASS/FAIL lines followed by a summary header.
    """
    goldens_dir = contracts_dir / "goldens"
    schemas_dir = contracts_dir / "schemas"
    compat_dir = contracts_dir / "compat"

    # Load allowlist (best-effort)
    allowlist_path = compat_dir / "field_allowlist.json"
    if allowlist_path.exists():
        allowlist: dict = json.loads(allowlist_path.read_bytes())
    else:
        allowlist = {}

    all_errors: list[str] = []
    results: list[tuple[str, list[str]]] = []

    # Check 0: protocol_version.json must be present
    protocol_version_path = compat_dir / "protocol_version.json"
    if not protocol_version_path.exists():
        pv_error = "MISSING: compat/protocol_version.json"
        all_errors.append(pv_error)
        print(f"FAIL   {pv_error}")

    if not goldens_dir.exists():
        return all_errors, 0

    # Collect all goldens
    golden_paths = sorted(goldens_dir.rglob("*.json"))
    golden_count = len(golden_paths)

    for golden_path in golden_paths:
        rel_path = str(golden_path.relative_to(contracts_dir))
        raw_bytes = golden_path.read_bytes()
        file_errors: list[str] = []

        # Check 1: canonical format
        file_errors.extend(check_canonical(raw_bytes, rel_path))

        # Parse JSON for subsequent checks
        try:
            data = json.loads(raw_bytes)
        except json.JSONDecodeError:
            results.append((rel_path, file_errors))
            all_errors.extend(file_errors)
            continue

        # Check 2: schema validation
        file_errors.extend(check_schema(data, golden_path.name, schemas_dir))

        # Check 3: determinism
        file_errors.extend(check_determinism(data, golden_path.name, allowlist))

        results.append((rel_path, file_errors))
        all_errors.extend(file_errors)

    # Print summary header then per-file results
    print(f"Contracts verified: {golden_count} goldens")
    for rel_path, file_errors in results:
        if file_errors:
            for err in file_errors:
                print(f"FAIL   {rel_path}: {err}")
        else:
            print(f"PASS   {rel_path}")

    return all_errors, golden_count


def main() -> None:
    """Parse --contracts-dir, call run_checks, print RESULT summary, sys.exit."""
    parser = argparse.ArgumentParser(
        description="Verify contract goldens against schemas and determinism rules."
    )
    parser.add_argument(
        "--contracts-dir",
        type=Path,
        default=CONTRACTS_DIR,
        help="Path to contracts directory (default: auto-detected from script location)",
    )
    args = parser.parse_args()

    errors, count = run_checks(args.contracts_dir)

    failed = len([e for e in errors])
    if errors:
        # Count distinct goldens that failed
        print(f"RESULT: FAIL ({failed}/{count} failed)")
        sys.exit(1)
    else:
        print(f"RESULT: PASS ({count}/{count})")
        sys.exit(0)


if __name__ == "__main__":
    main()
