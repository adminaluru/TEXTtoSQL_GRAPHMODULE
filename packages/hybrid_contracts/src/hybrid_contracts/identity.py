from .base import Contract, Identifier

# Roles and permission names are configuration values, not fixed application enums.
class AccessContext(Contract):
    """Internal input from authentication/policy. Construction does not prove trust."""
    subject_id: Identifier
    tenant_id: Identifier
    roles: tuple[Identifier, ...] = ()
    permissions: tuple[Identifier, ...] = ()
    policy_version: Identifier
