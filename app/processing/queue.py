import json
import redis.asyncio as redis

QUEUE_KEY         = "mailai:jobs:email"
INVOICE_QUEUE_KEY = "mailai:jobs:invoice"


async def enqueue_email_job(r: redis.Redis, tenant_id: int, email_id: int) -> None:
    payload = {"tenant_id": tenant_id, "email_id": email_id, "type": "process_email"}
    await r.lpush(QUEUE_KEY, json.dumps(payload))


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
