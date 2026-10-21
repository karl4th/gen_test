"""End-to-end invariants against the four-worker compose stack and PostgreSQL."""

import asyncio
import hmac
import json
import os
import unittest
from hashlib import sha256
from uuid import uuid4

import httpx
from sqlalchemy import text

from mdl_ml.database import create_engine


@unittest.skipUnless(os.getenv("RUN_DB_TESTS") == "1", "Requires docker compose up")
class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.client = httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=20)
        self.db = create_engine(
            os.getenv(
                "DATABASE_URL",
                "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/postgres",
            )
        )
        async with self.db.begin() as conn:
            self.user_id = (
                await conn.execute(
                    text("INSERT INTO users DEFAULT VALUES RETURNING id")
                )
            ).scalar_one()
        await self.configure("alpha", no_webhook=True)
        await self.configure("beta", no_webhook=True)

    async def asyncTearDown(self) -> None:
        async with self.db.connect() as conn:
            balance, ledger_sum = (
                await conn.execute(
                    text("""
                SELECT users.balance, coalesce(sum(ledger.delta), 0)
                FROM users LEFT JOIN ledger ON ledger.user_id = users.id
                WHERE users.id = :id GROUP BY users.id
            """),
                    {"id": self.user_id},
                )
            ).one()
        self.assertEqual(balance, ledger_sum)
        await self.client.aclose()
        await self.db.dispose()

    async def configure(self, provider: str, **changes) -> None:
        body = {
            "failure_rate": 0,
            "failure_type": "technical",
            "sync_failure": None,
            "delay_seconds": 0,
            "duplicate_webhooks": False,
            "no_webhook": False,
        }
        body.update(changes)
        port = 8001 if provider == "alpha" else 8002
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.put(
                f"http://127.0.0.1:{port}/testing/config",
                json=body,
            )
            response.raise_for_status()

    async def signed_post(self, path: str, payload: dict) -> httpx.Response:
        body = json.dumps(payload, separators=(",", ":")).encode()
        signature = hmac.new(b"postgres", body, sha256).hexdigest()
        return await self.client.post(
            path,
            content=body,
            headers={"Content-Type": "application/json", "X-Signature": signature},
        )

    async def credit(self, amount: int = 100) -> None:
        response = await self.signed_post(
            "/webhooks/payments",
            {
                "payment_id": str(uuid4()),
                "user_id": self.user_id,
                "tokens": amount,
                "status": "succeeded",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

    async def generate(self, model: str = "image-fast") -> str:
        response = await self.client.post(
            "/generate",
            json={
                "user_id": self.user_id,
                "model_slug": model,
                "prompt": "A test image",
                "ref_images": [],
            },
        )
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()["task_id"]

    async def wait_task(self, task_id: str, predicate) -> dict:
        for _ in range(100):
            response = await self.client.get(f"/tasks/{task_id}")
            self.assertEqual(response.status_code, 200, response.text)
            data = response.json()
            if predicate(data):
                return data
            await asyncio.sleep(0.1)
        self.fail(f"Task did not reach expected state: {data}")

    async def test_20_parallel_duplicate_payments_credit_once(self) -> None:
        payment_id = str(uuid4())
        payload = {
            "payment_id": payment_id,
            "user_id": self.user_id,
            "tokens": 41,
            "status": "succeeded",
        }
        responses = await asyncio.gather(
            *(self.signed_post("/webhooks/payments", payload) for _ in range(20))
        )
        self.assertTrue(
            all(r.status_code == 200 for r in responses), [r.text for r in responses]
        )
        balance = (await self.client.get(f"/users/{self.user_id}/balance")).json()
        self.assertEqual(balance["balance"], 41)
        async with self.db.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT count(*), coalesce(sum(delta), 0) "
                        "FROM ledger WHERE user_id = :id"
                    ),
                    {"id": self.user_id},
                )
            ).one()
        self.assertEqual(rows, (1, 41))

    async def test_10_parallel_final_failures_refund_once(self) -> None:
        await self.credit()
        await self.configure("alpha", sync_failure="503")
        await self.configure("beta", no_webhook=True)
        task_id = await self.generate()
        task = await self.wait_task(task_id, lambda x: len(x["attempts"]) == 2)
        second = next(a for a in task["attempts"] if a["provider"] == "beta")
        payload = {
            "external_id": second["id"],
            "status": "failed",
            "error_type": "technical",
        }
        responses = await asyncio.gather(
            *(self.signed_post("/webhooks/providers/beta", payload) for _ in range(10))
        )
        self.assertTrue(
            all(r.status_code == 200 for r in responses), [r.text for r in responses]
        )
        task = await self.wait_task(task_id, lambda x: x["status"] == "failed")
        self.assertEqual(task["status"], "failed")
        async with self.db.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT reason, delta FROM ledger "
                        "WHERE task_id = :id ORDER BY id"
                    ),
                    {"id": task_id},
                )
            ).all()
        self.assertEqual(rows, [("charge", -2), ("refund", 2)])
        self.assertEqual(
            (await self.client.get(f"/users/{self.user_id}/balance")).json()["balance"],
            100,
        )

    async def test_first_leg_failure_then_second_completes(self) -> None:
        await self.credit()
        await self.configure("alpha", failure_rate=1, failure_type="technical")
        await self.configure("beta")
        task_id = await self.generate()
        task = await self.wait_task(task_id, lambda x: x["status"] == "completed")
        self.assertEqual(len(task["attempts"]), 2)
        self.assertEqual({a["provider"] for a in task["attempts"]}, {"alpha", "beta"})
        self.assertEqual(
            {a["status"] for a in task["attempts"]}, {"failed", "succeeded"}
        )
        self.assertEqual(
            (await self.client.get(f"/users/{self.user_id}/balance")).json()["balance"],
            98,
        )

    async def test_synchronous_timeout_falls_back(self) -> None:
        await self.credit()
        await self.configure("alpha", sync_failure="timeout")
        await self.configure("beta")
        task_id = await self.generate()
        task = await self.wait_task(task_id, lambda x: x["status"] == "completed")
        self.assertEqual(len(task["attempts"]), 2)
        self.assertEqual(task["attempts"][0]["error_type"], "technical")
        self.assertEqual(task["attempts"][1]["status"], "succeeded")

    async def test_late_completion_after_watchdog_cannot_resurrect_refund(self) -> None:
        await self.credit()
        await self.configure("alpha", no_webhook=True)
        await self.configure("beta", no_webhook=True)
        task_id = await self.generate()
        first = (await self.wait_task(task_id, lambda x: len(x["attempts"]) == 1))[
            "attempts"
        ][0]
        async with self.db.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE attempts SET deadline_at = now() - interval '1 second' "
                    "WHERE id = :id"
                ),
                {"id": first["id"]},
            )
        task = await self.wait_task(task_id, lambda x: len(x["attempts"]) == 2)
        second = next(a for a in task["attempts"] if a["provider"] == "beta")
        async with self.db.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE attempts SET deadline_at = now() - interval '1 second' "
                    "WHERE id = :id"
                ),
                {"id": second["id"]},
            )
        await self.wait_task(task_id, lambda x: x["status"] == "failed")
        response = await self.signed_post(
            "/webhooks/providers/alpha",
            {
                "external_id": first["id"],
                "status": "completed",
                "result_url": "https://example.invalid/late",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        task = (await self.client.get(f"/tasks/{task_id}")).json()
        self.assertEqual(task["status"], "failed")
        self.assertIsNone(task["result_url"])
        self.assertEqual(
            next(a for a in task["attempts"] if a["id"] == first["id"])[
                "late_result_url"
            ],
            "https://example.invalid/late",
        )
        async with self.db.connect() as conn:
            count = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM ledger "
                        "WHERE task_id = :id AND reason = 'refund'"
                    ),
                    {"id": task_id},
                )
            ).scalar_one()
        self.assertEqual(count, 1)

    async def test_admission_and_reference_limits_do_not_overcharge(self) -> None:
        insufficient = await self.client.post(
            "/generate",
            json={
                "user_id": self.user_id,
                "model_slug": "image-fast",
                "prompt": "test",
                "ref_images": [],
            },
        )
        self.assertEqual(insufficient.status_code, 402)
        await self.credit(20)
        invalid = await self.client.post(
            "/generate",
            json={
                "user_id": self.user_id,
                "model_slug": "image-fast",
                "prompt": "test",
                "ref_images": ["x"] * 11,
            },
        )
        self.assertEqual(invalid.status_code, 400)
        tasks = await asyncio.gather(*(self.generate() for _ in range(3)))
        self.assertEqual(len(set(tasks)), 3)
        fourth = await self.client.post(
            "/generate",
            json={
                "user_id": self.user_id,
                "model_slug": "image-fast",
                "prompt": "test",
                "ref_images": [],
            },
        )
        self.assertEqual(fourth.status_code, 429)
        self.assertEqual(
            (await self.client.get(f"/users/{self.user_id}/balance")).json()["balance"],
            14,
        )

    async def test_failed_payment_and_moderation_skip(self) -> None:
        failed = await self.signed_post(
            "/webhooks/payments",
            {
                "payment_id": str(uuid4()),
                "user_id": self.user_id,
                "tokens": 100,
                "status": "failed",
            },
        )
        self.assertEqual(failed.status_code, 200)
        self.assertEqual(
            (await self.client.get(f"/users/{self.user_id}/balance")).json()["balance"],
            0,
        )
        await self.credit()
        await self.configure("beta", failure_rate=1, failure_type="moderation")
        task_id = await self.generate("video-long")
        task = await self.wait_task(task_id, lambda x: x["status"] == "failed")
        self.assertEqual(len(task["attempts"]), 1)
        self.assertEqual(task["attempts"][0]["provider"], "beta")
        self.assertEqual(
            (await self.client.get(f"/users/{self.user_id}/balance")).json()["balance"],
            100,
        )

    async def test_payload_failure_is_terminal_and_signature_is_required(self) -> None:
        await self.credit()
        unsigned = await self.client.post(
            "/webhooks/payments",
            json={
                "payment_id": str(uuid4()),
                "user_id": self.user_id,
                "tokens": 5,
                "status": "succeeded",
            },
        )
        self.assertEqual(unsigned.status_code, 401)
        await self.configure("alpha", failure_rate=1, failure_type="payload")
        task_id = await self.generate()
        task = await self.wait_task(task_id, lambda x: x["status"] == "failed")
        self.assertEqual(len(task["attempts"]), 1)
        self.assertEqual(task["error_type"], "payload")
        self.assertEqual(
            (await self.client.get(f"/users/{self.user_id}/balance")).json()["balance"],
            100,
        )
