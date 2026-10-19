# Running the tests

Two groups, separated by the `integration` marker (registered in `pytest.ini`,
`--strict-markers` so a misspelt marker fails instead of silently skipping).

| Group | Needs | Count (2026-10-15, after Step 7) | Runs in CI |
|-------|-------|--------------------|------------|
| Database-free (`not integration`) | nothing; no `.env` | 491 | yes |
| Integration (`integration`) | live PostgreSQL + Neo4j Golden Data from `.env` | 97 | no (A1.3) |

## PowerShell (from the repository root, venv active)

```powershell
# Reproducible install (known-good versions)
python -m pip install -r requirements.txt -c constraints.txt

# Everything (default; same as before markers existed)
python -m pytest -q

# Fast checks, no databases needed
python -m pytest -q -m "not integration"

# Live Golden Data checks only
python -m pytest -q -m integration

# Portable packages with no FlowOne app modules importable
foreach ($p in 'tenant_scope','clarification_state') { Push-Location "packages\$p"; python -I -m pytest -q -p no:cacheprovider tests; Pop-Location }
```

## Rules for new tests

- A test that connects to PostgreSQL or Neo4j (directly, through `Neo4jClient`,
  `PostgreSQLExecutor`, `build_research_agent` with real clients, or the API with a real agent)
  must carry `@pytest.mark.integration` (or module-level `pytestmark`).
- CI catches a missing marker: its placeholder settings point at a closed port, so an unmarked
  live test fails there instead of passing silently.
- Integration expected values must be verified by independent read-only SQL and recorded in
  `TEST_EVIDENCE.md`.

## CI

`.github/workflows/tests.yml` runs on push and pull request: installs with constraints, runs the
database-free tests, then the `tenant_scope` package in an isolated interpreter. `config.py`
validates connection settings at import, so CI supplies unusable placeholders (not secrets).
