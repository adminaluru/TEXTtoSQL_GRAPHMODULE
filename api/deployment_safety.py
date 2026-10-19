"""Composition layer: refuse to start a live deployment that is not safe (Step 7).

Production must have all of:

- per-caller identity (AUTH_MODE=jwt)                 -> identity_setup.py
- TLS to PostgreSQL (sslmode require / verify-ca / verify-full)
- a database login that cannot write: not a superuser, cannot bypass row
  security, no INSERT/UPDATE/DELETE/TRUNCATE on any table, cannot CREATE

In development the same findings are returned as warnings to log, so a
local superuser login keeps working but is never silent.
"""
from api.identity_setup import is_production, require_safe_auth_mode
from execution.models import DatabasePrivileges

SAFE_SSL_MODES = {"require", "verify-ca", "verify-full"}


def database_findings(settings, privileges: DatabasePrivileges) -> list[str]:
    findings = []
    if settings.postgres_sslmode not in SAFE_SSL_MODES:
        findings.append(f"PostgreSQL TLS is not required (POSTGRES_SSLMODE={settings.postgres_sslmode}).")
    if privileges.is_superuser:
        findings.append(f"Database login '{privileges.role}' is a superuser.")
    if privileges.bypasses_row_security:
        findings.append(f"Database login '{privileges.role}' bypasses row-level security.")
    if privileges.writable_tables:
        findings.append(f"Database login '{privileges.role}' can modify {privileges.writable_tables} table(s).")
    if privileges.can_create:
        findings.append(f"Database login '{privileges.role}' can create objects.")
    return findings


def check_deployment(settings, privileges: DatabasePrivileges) -> list[str]:
    """Raise in production when anything is unsafe; otherwise return warnings."""
    require_safe_auth_mode(settings)
    findings = database_findings(settings, privileges)
    if findings and is_production(settings):
        raise RuntimeError("Unsafe production deployment: " + " ".join(findings)
                           + " See docs/sql/flowone_readonly_role.sql.")
    return findings
