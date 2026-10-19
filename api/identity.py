"""HTTP layer for caller identity: headers in, AccessContext out.

    Authorization: Bearer <token>        required
    X-FlowOne-Tenant: <tenant id>        optional; needed when the caller
                                          belongs to more than one company

All checks live in the trusted_identity package; this file only reads the
headers and turns failures into 401 / 403 responses with generic messages.
"""
from fastapi import HTTPException, Request

from hybrid_contracts import AccessContext
from trusted_identity import AuthenticationError, IdentityVerifier, TenantSelectionError

TENANT_HEADER = "X-FlowOne-Tenant"


def bearer_token(request: Request) -> str | None:
    scheme, _, token = request.headers.get("Authorization", "").strip().partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


def identify_request(verifier: IdentityVerifier, request: Request):
    """Verified caller and the companies they may choose from (for GET /me)."""
    try:
        return verifier.identify(bearer_token(request))
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc),
                            headers={"WWW-Authenticate": "Bearer"}) from exc


def authenticate_request(verifier: IdentityVerifier, request: Request) -> AccessContext:
    try:
        return verifier.authenticate(bearer_token(request), request.headers.get(TENANT_HEADER))
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc),
                            headers={"WWW-Authenticate": "Bearer"}) from exc
    except TenantSelectionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
