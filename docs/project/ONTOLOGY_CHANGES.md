# Ontology changes (Neo4j)

The ontology lives only in Neo4j; there is no seed file in this repository. Every manual change is
recorded here so it can be repeated on another database and reviewed.

## 2026-10-19: confirm the claim adjustment link (A-OWN-1) — applied by the owner 2026-10-19

Owner approved after a data check: every `claim_inv_adj_link` row (18/18) reaches an existing invoice.
Only joins marked `confirmed` are used for company scoping (`planning/tenant_plan_scope.py`), so until
this runs, questions using this table are refused.

(`d_external_erp_invoice_load` no longer needs this: it is scoped by its own `company_Id` column since
the SQL compiler quotes mixed-case column names.)

Run in Neo4j Browser (http://localhost:7475), then restart the API (the ontology is cached):

```cypher
MATCH (:Table {key: 'public.claim_inv_adj_link'})-[r:JOINS_TO]->(:Table {key: 'public.erp_invoice_details'})
WHERE r.validation_status <> 'confirmed'
SET r.previous_validation_status = r.validation_status,
    r.validation_status = 'confirmed',
    r.confirmed_by = 'owner (A-OWN-1)',
    r.confirmed_on = '2026-10-19'
RETURN r.key AS key, r.previous_validation_status AS was, r.validation_status AS now
```

Expected result: one row, `was` = `inferred_actual_table_mapping`, `now` = `confirmed`.
To undo, set `r.validation_status = r.previous_validation_status`.
