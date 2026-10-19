"""Print a development access token (AUTH_MODE=jwt, shared secret).

Usage (PowerShell, venv active, from the repository root):

    python -m tools.issue_dev_token --subject alice --tenant tenant-acme --role credit_manager
    python -m tools.issue_dev_token --subject batch-job --tenant tenant-acme --service

Then call the API with:   Authorization: Bearer <token>
and, when the caller has several tenants:  X-FlowOne-Tenant: <tenant id>

Refuses to run with production settings.
"""
import argparse

from api.identity_setup import identity_settings_from
from config import settings
from trusted_identity import issue_dev_token


def main() -> None:
    parser = argparse.ArgumentParser(description="Issue a FlowOne development token.")
    parser.add_argument("--subject", required=True, help="user or service id")
    parser.add_argument("--tenant", action="append", required=True, dest="tenants",
                        help="logical tenant id; repeat for several companies")
    parser.add_argument("--role", action="append", default=[], dest="roles")
    parser.add_argument("--service", action="store_true", help="issue a service (app) token")
    parser.add_argument("--ttl", type=int, default=3600, help="lifetime in seconds")
    args = parser.parse_args()

    missing = [name for name, value in (
        ("IDENTITY_ISSUER", settings.identity_issuer),
        ("IDENTITY_AUDIENCE", settings.identity_audience),
        ("IDENTITY_SHARED_SECRET", settings.identity_shared_secret),
    ) if not value]
    if missing:
        raise SystemExit(
            "Development sign-in is not configured. Add these lines to .env "
            "(see docs/project/IDENTITY.md):\n  " + "\n  ".join(missing)
        )

    try:
        identity = identity_settings_from(settings)
        token = issue_dev_token(identity, subject=args.subject, tenants=args.tenants,
                                roles=args.roles, ttl_seconds=args.ttl, service=args.service)
    except (ValueError, PermissionError) as exc:
        # Settings validation explains what is unsafe (e.g. a short secret,
        # or production settings); never print the secret itself.
        raise SystemExit(f"Cannot issue a development token: {exc}")
    print(token)


if __name__ == "__main__":
    main()
