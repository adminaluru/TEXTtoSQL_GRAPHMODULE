"""Token verification: development (HS256) and provider keys (RS256 / Entra ID)."""
import ast
import time
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import ValidationError

from trusted_identity import (AuthenticationError, ClaimMapping, IdentitySettings,
                              IdentityVerifier, TenantSelectionError, issue_dev_token)

SECRET = "dev-secret-for-tests-only-0123456789abcdef"


def dev_settings(**kwargs):
    values = dict(environment="development", issuer="flowone-dev", audience="flowone-api",
                  algorithms=("HS256",), shared_secret=SECRET, policy_version="dev-1",
                  claims=ClaimMapping(principal_kind="kind"))
    values.update(kwargs)
    return IdentitySettings(**values)


def dev_token(settings=None, **kwargs):
    defaults = dict(subject="alice", tenants=["tenant-acme"], roles=["credit_manager"])
    defaults.update(kwargs)
    return issue_dev_token(settings or dev_settings(), **defaults)


# ------------------------------------------------------------------ happy paths

def test_dev_token_becomes_access_context():
    access = IdentityVerifier(dev_settings()).authenticate(dev_token())
    assert (access.subject_id, access.tenant_id, access.roles, access.policy_version) == (
        "user:alice", "tenant-acme", ("credit_manager",), "dev-1")


def test_service_tokens_get_their_own_identity_namespace():
    access = IdentityVerifier(dev_settings()).authenticate(dev_token(service=True))
    assert access.subject_id == "service:alice"


def test_rs256_provider_tokens_verify_with_the_published_key():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    settings = IdentitySettings.for_entra_id("dir-123", audience="api://flowone", policy_version="p1",
                                             claims=ClaimMapping(subject="oid", principal_kind="idtyp"))
    now = int(time.time())
    token = jwt.encode({"iss": settings.issuer, "aud": "api://flowone", "iat": now, "exp": now + 600,
                        "oid": "0000-oid", "tenants": ["tenant-acme"], "roles": ["sales"]},
                       key, algorithm="RS256")
    verifier = IdentityVerifier(settings, signing_key=lambda t: key.public_key())
    access = verifier.authenticate(token)
    assert (access.subject_id, access.tenant_id, access.roles) == ("user:0000-oid", "tenant-acme", ("sales",))


def test_entra_preset_points_at_the_directory():
    s = IdentitySettings.for_entra_id("dir-123", audience="api://flowone", policy_version="p1")
    assert s.issuer == "https://login.microsoftonline.com/dir-123/v2.0"
    assert s.jwks_url == "https://login.microsoftonline.com/dir-123/discovery/v2.0/keys"
    assert s.algorithms == ("RS256",) and s.claims.subject == "oid" and s.environment == "production"


# ------------------------------------------------------------- rejected tokens

def claims(**overrides):
    now = int(time.time())
    values = {"iss": "flowone-dev", "aud": "flowone-api", "iat": now, "exp": now + 600,
              "sub": "alice", "tenants": ["tenant-acme"]}
    values.update(overrides)
    return {k: v for k, v in values.items() if v is not None}


@pytest.mark.parametrize("token", [
    None, "", "   ", "not-a-jwt",
    jwt.encode(claims(exp=int(time.time()) - 3600), SECRET, algorithm="HS256"),     # expired
    jwt.encode(claims(aud="someone-else"), SECRET, algorithm="HS256"),               # wrong audience
    jwt.encode(claims(iss="evil-issuer"), SECRET, algorithm="HS256"),                # wrong issuer
    jwt.encode(claims(), "a-different-secret-0123456789abcdef!!", algorithm="HS256"),  # wrong key
    jwt.encode(claims(sub=None), SECRET, algorithm="HS256"),                         # no subject
    jwt.encode(claims(exp=None), SECRET, algorithm="HS256"),                         # no expiry
    jwt.encode(claims(), SECRET, algorithm="HS512"),                                 # algorithm not allowed
    jwt.encode(claims(), None, algorithm="none"),                                    # unsigned
])
def test_invalid_tokens_are_rejected_with_a_safe_message(token):
    with pytest.raises(AuthenticationError) as error:
        IdentityVerifier(dev_settings()).authenticate(token)
    assert str(error.value) == "Authentication is required. The access token is missing, invalid or expired."


def test_tampered_payload_is_rejected():
    header, payload, signature = dev_token().split(".")
    forged = jwt.encode(claims(tenants=["tenant-nova"]), SECRET, algorithm="HS256").split(".")[1]
    with pytest.raises(AuthenticationError):
        IdentityVerifier(dev_settings()).authenticate(f"{header}.{forged}.{signature}")


# ------------------------------------------------------------ tenant selection

def test_single_membership_is_used_automatically():
    assert IdentityVerifier(dev_settings()).authenticate(dev_token()).tenant_id == "tenant-acme"


def test_several_memberships_require_a_choice():
    token = dev_token(tenants=["tenant-acme", "tenant-nova"])
    verifier = IdentityVerifier(dev_settings())
    with pytest.raises(TenantSelectionError, match="choose"):
        verifier.authenticate(token)
    assert verifier.authenticate(token, "tenant-nova").tenant_id == "tenant-nova"


@pytest.mark.parametrize("requested", ["tenant-nova", "tenant-acme ", "TENANT-ACME"])
def test_spoofed_or_unknown_tenant_is_refused(requested):
    verifier = IdentityVerifier(dev_settings())
    if requested.strip() == "tenant-acme":
        assert verifier.authenticate(dev_token(), requested).tenant_id == "tenant-acme"
        return
    with pytest.raises(TenantSelectionError, match="do not have access"):
        verifier.authenticate(dev_token(), requested)


@pytest.mark.parametrize("tenants", [[], ["", None], "not-a-list-but-ok"])
def test_missing_or_malformed_memberships(tenants):
    token = jwt.encode(claims(tenants=tenants), SECRET, algorithm="HS256")
    verifier = IdentityVerifier(dev_settings())
    if tenants == "not-a-list-but-ok":
        assert verifier.authenticate(token, "not-a-list-but-ok").tenant_id == "not-a-list-but-ok"
    else:
        with pytest.raises(TenantSelectionError, match="any company"):
            verifier.authenticate(token)


def test_membership_source_overrides_the_token_claim():
    class FromDatabase:
        def memberships(self, principal):
            return frozenset({"tenant-from-db"}) if principal.subject_id == "alice" else frozenset()

    verifier = IdentityVerifier(dev_settings(), memberships=FromDatabase())
    assert verifier.authenticate(dev_token(tenants=["tenant-acme"])).tenant_id == "tenant-from-db"


# --------------------------------------------------------------- configuration

@pytest.mark.parametrize("overrides,message", [
    (dict(environment="production"), "Production requires"),
    (dict(algorithms=("none",)), "Unsupported"),
    (dict(algorithms=("RS256",)), "HMAC"),
    (dict(shared_secret="short"), "at least 32"),
    (dict(jwks_url="https://keys.example/jwks"), "exactly one key source"),
    (dict(shared_secret=None, jwks_url="http://insecure/keys", algorithms=("RS256",)), "https"),
    (dict(shared_secret=None, jwks_url="https://keys.example/jwks"), "asymmetric"),
])
def test_unsafe_configuration_is_rejected(overrides, message):
    with pytest.raises(ValidationError, match=message):
        dev_settings(**overrides)


def test_dev_tokens_cannot_be_issued_for_production():
    production = IdentitySettings.for_entra_id("dir", audience="api://x", policy_version="p")
    with pytest.raises(PermissionError, match="development settings"):
        issue_dev_token(production, subject="alice", tenants=["t"])


def test_secret_is_never_shown():
    assert SECRET not in repr(dev_settings()) and SECRET not in dev_settings().model_dump_json()


def test_package_imports_only_portable_dependencies():
    allowed = {"dataclasses", "typing", "time", "uuid", "pydantic", "jwt", "hybrid_contracts"}
    src = Path(__file__).parents[1] / "src" / "trusted_identity"
    for path in src.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                roots = {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                roots = {node.module.split(".")[0]}
            else:
                continue
            assert roots <= allowed, (path.name, roots)


def test_identify_lists_memberships_without_choosing_one():
    principal, tenants = IdentityVerifier(dev_settings()).identify(
        dev_token(tenants=["tenant-acme", "tenant-nova"]))
    assert (principal.subject_id, principal.kind) == ("alice", "user")
    assert tenants == frozenset({"tenant-acme", "tenant-nova"})


def test_identify_rejects_invalid_tokens():
    with pytest.raises(AuthenticationError):
        IdentityVerifier(dev_settings()).identify("garbage")
