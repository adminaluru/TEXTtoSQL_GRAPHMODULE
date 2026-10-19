# Trusted identity

Answers one question: **who is calling, and which company (tenant) are they acting for?**

It turns a bearer token into a `hybrid_contracts.AccessContext`. Nothing else in FlowOne
decides identity. Depends only on pydantic, PyJWT and `flowone-hybrid-contracts`; no web
framework, so any application (FastAPI, a worker, another RAG system) can use it.

Install: `python -m pip install -e '.[test]'`  Test: `python -m pytest tests`

## The three files

| File | Job |
|------|-----|
| `settings.py` | What to trust: issuer, audience, allowed algorithms, where the keys are, which claim holds what. Rejects unsafe combinations. |
| `verifier.py` | Checks a token and picks the active tenant. Returns `AccessContext` or raises `AuthenticationError` (401) / `TenantSelectionError` (403). |
| `dev_tokens.py` | Makes development tokens. Refuses production settings. |

## Development now, Entra ID later: only settings change

```python
# Development: shared secret, tokens from issue_dev_token(...)
IdentitySettings(environment="development", issuer="flowone-dev", audience="flowone-api",
                 algorithms=("HS256",), shared_secret="<32+ random characters>",
                 policy_version="dev-1")

# Production with Microsoft Entra ID: provider keys (JWKS), RS256, user id in "oid"
IdentitySettings.for_entra_id(directory_tenant_id="<directory id>",
                              audience="api://<app id>", policy_version="2026-10")
```

Production settings refuse shared secrets and HS* algorithms, so a development token can
never be accepted by a live deployment.

## Rules the verifier always applies

1. Signature, issuer, audience, expiry and issued-at are required; `alg: none` is never accepted.
2. Tenants come only from the verified token (or a `MembershipSource`, e.g. a database lookup),
   never from the question or request body.
3. The caller may choose the active tenant (e.g. an `X-FlowOne-Tenant` header), but only one
   of their memberships. One membership is used automatically; several require a choice.
4. Users and services get separate identities: `user:<id>` and `service:<id>`.
5. Error messages are generic; the reason is never revealed to the caller.

## Not done here

Roles are passed through as given by the token; what each role may see is the access-policy
step (Step 6). Membership data for production (who belongs to which company) is not decided
yet; supply it through a `MembershipSource`.
