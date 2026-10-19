# Tenant scope

Independent package: depends only on pydantic and `flowone-hybrid-contracts`. No FastAPI,
database driver, ontology or FlowOne application imports.

Install: python -m pip install -e '.[test]'
Test: python -m pytest tests

## What it does

Maps a **logical** tenant (`AccessContext.tenant_id`) to the **physical** owner key it owns,
and returns equality `RowPredicate`s for tables whose ownership column is declared. A SQL
adapter must bind the value as a parameter and apply it before retrieval.

- POC rule (decision D-002): one company per tenant, enforced in both directions.
- Unknown tenants and tables with no declared ownership column raise `TenantScopeError`;
  callers must fail closed rather than run unscoped.
- Tenant IDs and owner keys are separate on purpose; never derive one from the other or from
  record names/ID formats.

## What it does not do

It does not authenticate anyone: `AccessContext` must come from verified identity (Step 5).
It does not supply the mapping data; the composition root loads it from an approved source.
Role, field, document and graph permissions are separate (Step 6).
