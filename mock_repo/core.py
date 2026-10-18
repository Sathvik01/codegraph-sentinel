"""Core identity primitives for the fixture repository."""


def validate_identity(token: str) -> bool:
    """Hub function: every entry path funnels through here."""
    if not token:
        return False
    cleaned = token.strip()
    if len(cleaned) < 8:
        return False
    return cleaned.isprintable()


def checksum(payload: bytes) -> int:
    """Stable content checksum used by the job runner."""
    total = 0
    for byte in payload:
        total = (total * 31 + byte) % 2147483647
    return total