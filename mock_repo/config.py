"""Tenant configuration parsing."""

ALLOWED_KEYS = {"tenant", "region", "quota"}


def parse_config(raw: str, token: str) -> dict:
    """Parse config text, but only for an authenticated identity."""
    validated = validate_identity(token)
    if not validated:
        raise PermissionError("identity rejected")
    settings = {}
    for line in raw.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in ALLOWED_KEYS:
            settings[key] = value.strip()
    return settings