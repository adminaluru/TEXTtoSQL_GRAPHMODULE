# Running the tests

Two groups, separated by the `integration` marker (registered in `pytest.ini`,
`--strict-markers` so a misspelt marker fails instead of silently skipping).

| Group | Needs | Count (2026-10-20) | Runs in CI |
|-------|-------|--------------------|------------|
| Database-free (`not integration`) | nothing; no `.env` | 893 | yes (`unit` job) |
| Integration (`integration`) | PostgreSQL + Neo4j with the Golden Data: from `.env` locally, or the fixtures in `fixtures/golden` | 107 | yes (`integration` job, A1.3) |

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

`.github/workflows/tests.yml` runs on push and pull request:

- `unit`: installs with constraints, runs the database-free tests, then each portable package in an
  isolated interpreter. `config.py` validates connection settings at import, so this job supplies
  unusable placeholders (not secrets).
- `integration` (A1.3): starts PostgreSQL 17.6 and Neo4j 5.26.28 containers, waits until Neo4j has
  finished starting ("Started."), loads `fixtures/golden/v1`, creates the read-only login with
  `docs/sql/flowone_readonly_role.sql`, and runs the integration tests as that login. Passwords in the
  workflow protect throwaway containers only.
- `ui`: type-check, unit tests and production build of the chat UI.

## Golden Data fixtures (A1.3)

`fixtures/golden/v1/` holds the synthetic Golden Data: `postgres.sql` (the 40 FlowOne tables in the
ontology, structure and rows; other projects' tables in the same database are never exported) and
`neo4j.json` (the whole ontology: constraints, indexes, nodes, relationships).

```powershell
# After changing the development Golden Data or ontology: re-export, review the diff, commit.
python -m tools.golden_fixtures export

# Load into disposable test databases (refuses non-empty targets unless --replace).
python -m tools.golden_fixtures load --wait-seconds 300 `
  --postgres-dsn postgresql://postgres:<admin password>@localhost:5433/flowone_test `
  --neo4j-uri bolt://localhost:7690 --neo4j-user neo4j --neo4j-password <password>
```

Lessons from making the fresh-container run reliable (all built into the job and the loader):
Neo4j answers simple queries before start-up has finished (wait for "Started."); a freshly loaded
Neo4j has no statistics, so path queries can take over a minute until `db.prepareForReplanning()`
runs (the loader does); and shared runners need longer Neo4j timeouts than the application defaults.
