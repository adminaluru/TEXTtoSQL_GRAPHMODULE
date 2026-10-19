"""Identity verification settings: one model for development and production.

Development: tokens signed with a shared secret (HS256), issued locally.
Production: tokens from an OpenID Connect provider such as Microsoft Entra ID,
verified with the provider's published keys (JWKS, RS256).

Only configuration differs; verification rules are the same. Production
refuses shared secrets and HMAC algorithms.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

Environment = Literal["development", "production"]

ASYMMETRIC = {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512"}
SYMMETRIC = {"HS256", "HS384", "HS512"}


class ClaimMapping(BaseModel):
    """Where each fact lives in the token. Providers name claims differently."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    subject: str = "sub"
    # List of logical tenant IDs the caller belongs to. Ignored when a
    # MembershipSource supplies memberships instead (e.g. from a database).
    tenants: str = "tenants"
    roles: str = "roles"
    # Optional claim telling a service (app-only) token from a user token,
    # e.g. Entra ID "idtyp" = "app".
    principal_kind: str | None = None
    service_kind_values: tuple[str, ...] = ("app", "service")


class IdentitySettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    environment: Environment
    issuer: str = Field(min_length=1)
    audience: str = Field(min_length=1)
    algorithms: tuple[str, ...] = Field(min_length=1)
    shared_secret: SecretStr | None = None
    jwks_url: str | None = None
    claims: ClaimMapping = ClaimMapping()
    leeway_seconds: int = Field(default=30, ge=0, le=300)
    # Changes whenever role/permission policy changes, so state created
    # under an older policy (e.g. pending clarifications) is not reused.
    policy_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self):
        algorithms = set(self.algorithms)
        unknown = algorithms - ASYMMETRIC - SYMMETRIC
        if unknown or "none" in {a.lower() for a in self.algorithms}:
            raise ValueError(f"Unsupported signing algorithms: {sorted(unknown) or ['none']}")
        if (self.shared_secret is None) == (self.jwks_url is None):
            raise ValueError("Configure exactly one key source: shared_secret or jwks_url")
        if self.shared_secret is not None:
            if not algorithms <= SYMMETRIC:
                raise ValueError("A shared secret can only verify HMAC (HS*) algorithms")
            if len(self.shared_secret.get_secret_value()) < 32:
                raise ValueError("The shared secret must be at least 32 characters")
        if self.jwks_url is not None:
            if not algorithms <= ASYMMETRIC:
                raise ValueError("JWKS keys verify asymmetric algorithms only")
            if not self.jwks_url.startswith("https://"):
                raise ValueError("jwks_url must use https")
        if self.environment == "production" and (self.shared_secret is not None or algorithms & SYMMETRIC):
            raise ValueError("Production requires provider-signed tokens (JWKS); shared secrets are development-only")
        return self

    @classmethod
    def for_entra_id(cls, directory_tenant_id: str, audience: str, policy_version: str,
                     environment: Environment = "production",
                     claims: ClaimMapping | None = None) -> "IdentitySettings":
        """Microsoft Entra ID (v2.0 tokens) for one directory tenant.

        audience is the application (client) ID or its Application ID URI.
        Users are identified by the immutable object ID ("oid"); app-only
        (service) tokens carry idtyp="app". Tenant memberships are not an
        Entra concept here: supply them via a MembershipSource or a custom
        claim configured in `claims.tenants`.
        """
        base = f"https://login.microsoftonline.com/{directory_tenant_id}"
        return cls(
            environment=environment,
            issuer=f"{base}/v2.0",
            audience=audience,
            algorithms=("RS256",),
            jwks_url=f"{base}/discovery/v2.0/keys",
            claims=claims or ClaimMapping(subject="oid", principal_kind="idtyp"),
            policy_version=policy_version,
        )
