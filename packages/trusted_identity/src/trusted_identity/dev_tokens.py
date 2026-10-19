"""Issue development tokens (HS256) that the verifier accepts.

Refuses to run with production settings: a production deployment must only
accept tokens signed by its identity provider.
"""
import time
import uuid

import jwt

from .settings import IdentitySettings


def issue_dev_token(settings: IdentitySettings, subject: str, tenants: list[str],
                    roles: list[str] = (), ttl_seconds: int = 3600, service: bool = False,
                    now: float | None = None) -> str:
    if settings.environment != "development" or settings.shared_secret is None:
        raise PermissionError("Development tokens can only be issued with development settings")
    if not subject.strip():
        raise ValueError("subject is required")
    if not 60 <= ttl_seconds <= 24 * 3600:
        raise ValueError("ttl_seconds must be between 1 minute and 24 hours")
    issued = int(now if now is not None else time.time())
    claims = {
        "iss": settings.issuer,
        "aud": settings.audience,
        "iat": issued,
        "nbf": issued,
        "exp": issued + ttl_seconds,
        "jti": uuid.uuid4().hex,
        settings.claims.subject: subject,
        settings.claims.tenants: list(tenants),
        settings.claims.roles: list(roles),
    }
    if service and settings.claims.principal_kind:
        claims[settings.claims.principal_kind] = settings.claims.service_kind_values[0]
    return jwt.encode(claims, settings.shared_secret.get_secret_value(), algorithm=settings.algorithms[0])
