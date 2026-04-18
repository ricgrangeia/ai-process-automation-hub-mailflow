import json
import redis.asyncio as redis

QUEUE_KEY         = "mailai:jobs:email"   # legacy — no longer consumed; kept for reference
INVOICE_QUEUE_KEY = "mailai:jobs:invoice"


async def enqueue_email_job(r: redis.Redis, tenant_id: int, email_id: int) -> None:
    """Legacy — pushes to old QUEUE_KEY. Use enqueue_classification_job instead."""
    payload = {"tenant_id": tenant_id, "email_id": email_id, "type": "process_email"}
    await r.lpush(QUEUE_KEY, json.dumps(payload))


async def enqueue_classification_job(r: redis.Redis, tenant_id: int, email_id: int) -> None:
    """Enqueue a general email classification job to the invoice-worker queue."""
    payload = {"tenant_id": tenant_id, "email_id": email_id, "type": "process_email"}
    await r.lpush(INVOICE_QUEUE_KEY, json.dumps(payload))


async def enqueue_invoice_job(
    r: redis.Redis, tenant_id: int, email_id: int, classification: str
) -> None:
    payload = {
        "tenant_id":      tenant_id,
        "email_id":       email_id,
        "type":           "process_invoice",
        "classification": classification,
    }
    await r.lpush(INVOICE_QUEUE_KEY, json.dumps(payload))
