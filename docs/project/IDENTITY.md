# Caller identity: how to run it

Decision D-008: development uses simple signed tokens; production uses an identity provider
(Microsoft Entra ID or any OpenID Connect provider) by changing configuration only.

## Development (PowerShell, venv active, repository root)

1. Add to `.env` (pick your own secret, at least 32 characters; never commit it):

```
AUTH_MODE=jwt
IDENTITY_ISSUER=flowone-dev
IDENTITY_AUDIENCE=flowone-api
IDENTITY_SHARED_SECRET=<32+ random characters>
TENANT_SCOPE_FILE=docs/examples/tenant_scope.dev.json
```

   A random secret: `python -c "import secrets; print(secrets.token_urlsafe(48))"`

2. Issue a token for a test user:

```powershell
$token = python -m tools.issue_dev_token --subject alice --tenant tenant-acme --role credit_manager
```

3. Call the API:

```powershell
$headers = @{ Authorization = "Bearer $token" }
Invoke-RestMethod -Method Post -Uri http://localhost:8000/ask -Headers $headers `
  -ContentType "application/json" `
  -Body '{"question": "What is the total claim amount for Blue Prism?", "conversation_id": "conv-1"}'
```

   If the answer offers choices, continue with `POST /clarify`
   (`conversation_id`, `clarification_id`, `option_id`) using the same token.

   A user in several companies adds `X-FlowOne-Tenant: tenant-nova` to choose one.

## Production with Microsoft Entra ID (no code change)

```
APP_ENV=production
AUTH_MODE=jwt
IDENTITY_ENTRA_DIRECTORY_ID=<directory (tenant) id>
IDENTITY_AUDIENCE=api://<application (client) id>
IDENTITY_SUBJECT_CLAIM=oid
IDENTITY_KIND_CLAIM=idtyp
IDENTITY_POLICY_VERSION=<change when role policy changes>
TENANT_SCOPE_FILE=<approved tenant mapping>
```

Remove `IDENTITY_SHARED_SECRET`. The application refuses to start in production with the shared
API key or a shared secret.

Still to decide before going live: where company memberships come from (a claim configured in
Entra ID, or a database lookup through `MembershipSource`), and the production tenant mapping.

## What gets rejected

| Situation | Response |
|-----------|----------|
| No token, malformed, expired, wrong issuer/audience, wrong key, unsigned (`alg: none`) | 401, generic message |
| Tenant header names a company the caller does not belong to | 403 |
| Caller belongs to several companies and chose none | 403 "Please choose which company to use." |
| Tenant or role sent in the request body | 422 (field not allowed) |
| Someone else's, expired or already used clarification choice | 409 |
