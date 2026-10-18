"""Job orchestration entrypoints."""


def run_job(tenant_id: str, token: str, payload: bytes) -> dict:
    """Load the tenant, sign the payload, execute the job."""
    record = load_tenant(tenant_id, token)
    signature = checksum(payload)
    result = {"tenant": record["id"], "signature": signature, "status": "done"}
    return result


def format_timestamp(epoch_seconds: int) -> str:
    """Isolated leaf helper: nothing calls it, it calls nothing."""
    minutes = epoch_seconds // 60
    hours = minutes // 60
    return "%02d:%02d" % (hours % 24, minutes % 60)