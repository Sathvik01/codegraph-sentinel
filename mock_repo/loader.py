"""Tenant data loading."""

MAX_BATCH = 500


def load_tenant(tenant_id: str, token: str) -> dict:
    """Load a tenant record, gated on identity validation."""
    if not validate_identity(token):
        raise PermissionError("identity rejected")
    if not tenant_id:
        raise ValueError("missing tenant")
    record = {"id": tenant_id, "rows": []}
    return record