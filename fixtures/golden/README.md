# Golden Data fixtures

Synthetic data for the integration tests (action A1.3). Generated, not edited by hand:

    python -m tools.golden_fixtures export      # from the databases in .env
    python -m tools.golden_fixtures load ...    # into disposable test databases (see docs/project/TESTING.md)

| Version | Contents | Exported |
|---------|----------|----------|
| v1 | PostgreSQL: 40 FlowOne tables, 1,886 rows. Neo4j: 729 nodes, 1,067 relationships, 4 constraints, 4 indexes. Includes the `claim_inv_adj_link` join confirmed on 2026-10-19 (A-OWN-1). | 2026-10-20 |

All values are synthetic (customer names such as "Blue Prism Technologies LE-1001", e-mail addresses on
`.example` domains). Only tables in the ontology are exported; other projects' tables that share the
development database are left out. Create a new version folder (v2, ...) when a change would alter
what existing tests expect, so older fixtures stay reproducible.
