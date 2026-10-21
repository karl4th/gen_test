import asyncio
import hmac
import json
import logging
import os
import random
from hashlib import sha256
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)
app = FastAPI(title="Fake generation provider")
NAME = os.getenv("PROVIDER_NAME", "alpha")
CALLBACK_BASE = os.getenv("PUBLIC_CALLBACK_BASE_URL", "http://api:8000")
SECRET = os.getenv("WEBHOOK_SECRET", "postgres")


class Behavior(BaseModel):
    failure_rate: float = Field(default=0, ge=0, le=1)
    failure_type: str = "technical"
    sync_failure: str | None = None
    delay_seconds: float | None = Field(default=None, ge=0)
    duplicate_webhooks: bool = False
    no_webhook: bool = False


class ProviderTask(BaseModel):
    external_id: str
    prompt: str
    ref_images: list[str]


behavior = Behavior()
active: set[asyncio.Task[None]] = set()
accepted: set[str] = set()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "provider": NAME}


@app.put("/testing/config", response_model=Behavior)
async def configure(value: Behavior) -> Behavior:
    if value.failure_type not in ("technical", "moderation", "payload"):
        raise HTTPException(422, "Invalid failure type")
    if value.sync_failure not in (None, "503", "timeout"):
        raise HTTPException(422, "Invalid synchronous failure")
    global behavior
    behavior = value
    return behavior


async def send_webhook(
    external_id: str, result: dict[str, str], delay: float, duplicate: bool
) -> None:
    await asyncio.sleep(delay)
    body = json.dumps(result, separators=(",", ":")).encode()
    signature = hmac.new(SECRET.encode(), body, sha256).hexdigest()

    async def send() -> None:
        try:
            async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                response = await client.post(
                    f"{CALLBACK_BASE}/webhooks/providers/{NAME}",
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Signature": signature,
                    },
                )
                response.raise_for_status()
        except httpx.HTTPError:
            log.exception("Webhook delivery failed for %s", external_id)

    if duplicate:
        await asyncio.gather(send(), send())
    else:
        await send()


@app.post("/tasks", status_code=202)
async def submit(data: ProviderTask) -> dict[str, str]:
    config = behavior.model_copy()
    if data.external_id in accepted:
        return {"external_id": data.external_id}
    if config.sync_failure == "503":
        raise HTTPException(503, "Configured provider failure")
    if config.sync_failure == "timeout":
        await asyncio.sleep(30)
    accepted.add(data.external_id)
    if not config.no_webhook:
        failed = random.random() < config.failure_rate
        result = {
            "external_id": data.external_id,
            "status": "failed" if failed else "completed",
        }
        if failed:
            result["error_type"] = config.failure_type
        else:
            result["result_url"] = f"https://example.invalid/{NAME}/{uuid4()}"
        delay = (
            config.delay_seconds
            if config.delay_seconds is not None
            else random.uniform(2, 20)
        )
        task = asyncio.create_task(
            send_webhook(data.external_id, result, delay, config.duplicate_webhooks)
        )
        active.add(task)
        task.add_done_callback(active.discard)
    return {"external_id": data.external_id}
