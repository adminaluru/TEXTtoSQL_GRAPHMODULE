"""Bearer token -> verified AccessContext for one active tenant.

Never trusts tenants, roles or identities from question text or request
bodies: only a verified token (and, optionally, a MembershipSource) decides
who the caller is and which tenants they belong to. The caller may *choose*
the active tenant, but only among those verified memberships.
"""
from dataclasses import dataclass
from typing import Any, Callable, Protocol

import jwt

from hybrid_contracts import AccessContext

from .settings import IdentitySettings

INVALID_TOKEN = "Authentication is required. The access token is missing, invalid or expired."
NO_TENANT = "You do not have access to any company in this application."
CHOOSE_TENANT = "Please choose which company to use."
NOT_A_MEMBER = "You do not have access to the selected company."


class AuthenticationError(Exception):
    """401: the caller could not be identified. Message is safe to return."""


class TenantSelectionError(PermissionError):
    """403: identified, but the active tenant is not one of their memberships."""


@dataclass(frozen=True)
class Principal:
    subject_id: str
    kind: str                      # "user" or "service"
    roles: tuple[str, ...]
    claims: dict[str, Any]


class MembershipSource(Protocol):
    def memberships(self, principal: Principal) -> frozenset[str]: ...


class ClaimMemberships:
    """Memberships listed in the token itself (development, or a custom claim)."""

    def __init__(self, claim: str):
        self._claim = claim

    def memberships(self, principal: Principal) -> frozenset[str]:
        value = principal.claims.get(self._claim, ())
        if isinstance(value, str):
            value = (value,)
        if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) and v for v in value):
            return frozenset()
        return frozenset(value)


class IdentityVerifier:
    def __init__(self, settings: IdentitySettings, memberships: MembershipSource | None = None,
                 signing_key: Callable[[str], Any] | None = None):
        """
        signing_key(token) returns the verification key. Defaults: the shared
        secret, or the JWKS key matching the token's "kid" (fetched and cached
        by PyJWT). Injectable for tests and for custom key stores.
        """
        self._settings = settings
        self._memberships = memberships or ClaimMemberships(settings.claims.tenants)
        if signing_key is not None:
            self._signing_key = signing_key
        elif settings.shared_secret is not None:
            secret = settings.shared_secret.get_secret_value()
            self._signing_key = lambda token: secret
        else:
            client = jwt.PyJWKClient(settings.jwks_url, cache_keys=True)
            self._signing_key = lambda token: client.get_signing_key_from_jwt(token).key

    def verify(self, token: str | None) -> Principal:
        if not token or not token.strip():
            raise AuthenticationError(INVALID_TOKEN)
        s = self._settings
        try:
            claims = jwt.decode(
                token.strip(),
                key=self._signing_key(token.strip()),
                algorithms=list(s.algorithms),
                audience=s.audience,
                issuer=s.issuer,
                leeway=s.leeway_seconds,
                options={"require": ["exp", "iat", "iss", "aud", s.claims.subject],
                         "verify_signature": True},
            )
        except (jwt.PyJWTError, ValueError, TypeError) as exc:
            raise AuthenticationError(INVALID_TOKEN) from exc
        subject = claims.get(s.claims.subject)
        if not isinstance(subject, str) or not subject.strip():
            raise AuthenticationError(INVALID_TOKEN)
        roles = claims.get(s.claims.roles, ())
        roles = (roles,) if isinstance(roles, str) else tuple(r for r in roles if isinstance(r, str) and r)
        kind_value = claims.get(s.claims.principal_kind) if s.claims.principal_kind else None
        kind = "service" if kind_value in s.claims.service_kind_values else "user"
        return Principal(subject_id=subject.strip(), kind=kind, roles=roles, claims=claims)

    def identify(self, token: str | None) -> tuple[Principal, frozenset[str]]:
        """Who is calling and which tenants they may choose from (no tenant selected)."""
        principal = self.verify(token)
        return principal, self._memberships.memberships(principal)

    def authenticate(self, token: str | None, requested_tenant: str | None = None) -> AccessContext:
        principal, memberships = self.identify(token)
        if not memberships:
            raise TenantSelectionError(NO_TENANT)
        requested = (requested_tenant or "").strip()
        if requested:
            if requested not in memberships:
                raise TenantSelectionError(NOT_A_MEMBER)
            tenant = requested
        elif len(memberships) == 1:
            (tenant,) = memberships
        else:
            raise TenantSelectionError(CHOOSE_TENANT)
        return AccessContext(
            # Users and services never share an identity namespace.
            subject_id=f"{principal.kind}:{principal.subject_id}",
            tenant_id=tenant,
            roles=principal.roles,
            policy_version=self._settings.policy_version,
        )
