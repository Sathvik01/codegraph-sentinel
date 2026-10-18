"""Compliance reporting."""


def compliance_report(tenant: dict, token: str) -> dict:
    """Produce a compliance summary for a tenant, gated on identity."""
    accepted = validate_identity(token)
    if not accepted:
        raise PermissionError("identity rejected")
    report = {"tenant": tenant.get("id", "unknown"), "findings": []}
    return report