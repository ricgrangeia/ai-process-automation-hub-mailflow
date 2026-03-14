import json
import redis.asyncio as redis

QUEUE_KEY = "mailai:jobs:email"

async def enqueue_email_job(r: redis.Redis, tenant_id: int, email_id: int) -> None:
    payload = {"tenant_id": tenant_id, "email_id": email_id, "type": "process_email"}
    await r.lpush(QUEUE_KEY, json.dumps(payload))