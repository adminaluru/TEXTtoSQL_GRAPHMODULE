"""Composition layer: build identity and tenant-scope objects from settings.

Kept separate from the HTTP layer (api/identity.py) and from the settings
themselves (config.py), so each piece has one job:

    config.py          plain values read from the environment
    identity_setup.py  turns those values into verifier / scope objects  (this file)
    identity.py        reads HTTP headers and calls the verifier
    application.py     routes; uses the verified caller per request
"""
import json
from pathlib import Path

from tenant_scope import TenantScopeMap, TenantScopeResolver
from trusted_identity import ClaimMapping, IdentitySettings, IdentityVerifier

PRODUCTION_ENVIRONMENTS = {"prod", "production", "live"}


def is_production(settings) -> bool:
    return str(getattr(settings, "app_env", "")).strip().lower() in PRODUCTION_ENVIRONMENTS


def require_safe_auth_mode(settings) -> None:
    """Fail closed: a live deployment must identify every caller."""
    if is_production(settings) and settings.auth_mode != "jwt":
        raise RuntimeError("Production requires AUTH_MODE=jwt; the shared API key has no per-user identity.")


def identity_settings_from(settings) -> IdentitySettings:
    claims = ClaimMapping(
        subject=settings.identity_subject_claim,
        tenants=settings.identity_tenants_claim,
        roles=settings.identity_roles_claim,
        principal_kind=settings.identity_kind_claim or None,
    )
    environment = "production" if is_production(settings) else "development"
    if settings.identity_entra_directory_id:
        return IdentitySettings.for_entra_id(
            directory_tenant_id=settings.identity_entra_directory_id,
            audience=settings.identity_audience or "",
            policy_version=settings.identity_policy_version,
            environment=environment,
            claims=claims,
        )
    # IdentitySettings rejects unsafe combinations, including shared
    # secrets in production.
    return IdentitySettings(
        environment=environment,
        issuer=settings.identity_issuer or "",
        audience=settings.identity_audience or "",
        algorithms=tuple(settings.identity_algorithms),
        shared_secret=settings.identity_shared_secret,
        jwks_url=settings.identity_jwks_url,
        claims=claims,
        policy_version=settings.identity_policy_version,
    )


def identity_verifier_from(settings) -> IdentityVerifier:
    return IdentityVerifier(identity_settings_from(settings))


def tenant_resolver_from_file(path: str | None) -> TenantScopeResolver:
    if not path:
        raise RuntimeError("TENANT_SCOPE_FILE is required when AUTH_MODE=jwt.")
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    data = {key: value for key, value in data.items() if not key.startswith("_")}
    return TenantScopeResolver(TenantScopeMap.model_validate(data))
