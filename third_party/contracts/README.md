# Orchestrator Contract Governance

This directory defines the authoritative contract layer for the orchestrator pipeline.
It provides stable golden fixtures, schema copies, and enforcement tooling for
determinism and canonical formatting.

## Protocol Version

`contracts/compat/protocol_version.json` pins the contract protocol version:

```json
{"contracts_commit":"<git-sha>","protocol":"1.0.0"}
```

**Fields:**

| Field | Description |
|---|---|
| `protocol` | Semantic version of the contract protocol (`MAJOR.MINOR.PATCH`) |
| `contracts_commit` | Git SHA of the commit that last updated the contracts layer |

**How other repos should pin against it:**

1. Record `protocol` + `contracts_commit` in your own lockfile or CI config.
2. On each update, run `make verify-contracts` to ensure all goldens still pass.
3. A `MAJOR` bump means breaking schema changes — update downstream consumers.
4. A `MINOR` bump means backward-compatible additions — safe to consume.

## Structure

```
contracts/
  schemas/              # Copies of schemas/*.v1.json (source of truth: schemas/)
  goldens/
    minimal/            # Smallest valid instance per artifact type (7 schemas, 7 goldens)
    e2e/
      example_episode/  # Canonicalized from examples/phase0/expected/
  compat/
    protocol_version.json   # Protocol version pin
    field_allowlist.json    # Allowlist for determinism exceptions (currently empty)
  tools/
    verify_contracts.py     # Tier-A verifier script
  README.md
```

## Schema Scope

All 7 schemas are in scope and covered by minimal goldens:

| Schema | Golden |
|---|---|
| `Script.v1.json` | `goldens/minimal/Script.json` |
| `ShotList.v1.json` | `goldens/minimal/ShotList.json` |
| `AssetManifest.v1.json` | `goldens/minimal/AssetManifest.json` |
| `RenderPlan.v1.json` | `goldens/minimal/RenderPlan.json` |
| `RenderOutput.v1.json` | `goldens/minimal/RenderOutput.json` |
| `RenderPackage.v1.json` | `goldens/minimal/RenderPackage.json` |
| `EpisodeBundle.v1.json` | `goldens/minimal/EpisodeBundle.json` |

## Golden Format Rules

All golden files must be in **canonical format**:

- Keys sorted lexicographically (recursive)
- Separators: `","` and `":"` (no spaces)
- UTF-8 encoding
- Trailing `\n`

Use `make verify-contracts` to validate all goldens.

## Determinism Policy

Golden files must not contain non-deterministic values. The verifier flags:

- ISO datetimes — **unless** the epoch sentinel `"1970-01-01T00:00:00Z"` is used
- UUID-pattern strings
- `file://` URIs where the path does **not** start with `placeholder`
  (i.e., `file:///placeholder/...` is allowed as a stable fixture URI)
- OS absolute paths (`/path/...` or `C:\path\...`)

All goldens use deterministic sentinel values. The `field_allowlist.json` is
currently empty (no entries needed).

### URI Policy

E2E goldens use `file:///placeholder/...` URIs verbatim from the example
fixtures. These are stable placeholder paths, not host-specific. The verifier
allows `file:///placeholder/...` because the determinism regex is anchored to
`file:///[^p]` (flags only non-placeholder file URIs).

Do **not** rewrite these to `placeholder://...` — that would introduce a URI
scheme not defined by any schema and would diverge from actual pipeline output.

## Running the Verifier

```bash
make verify-contracts
# or directly:
python contracts/tools/verify_contracts.py
```
