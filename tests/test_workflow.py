"""End-to-end invariants against the four-worker compose stack and PostgreSQL."""

import asyncio
import hmac
import json
import os
import random
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

    async def test_conflicting_payment_replay_preserves_first_credit(self) -> None:
        payment_id = str(uuid4())
        original = {
            "payment_id": payment_id,
            "user_id": self.user_id,
            "tokens": 7,
            "status": "succeeded",
        }
        self.assertEqual(
            (await self.signed_post("/webhooks/payments", original)).status_code, 200
        )
        changed = dict(original, tokens=8)
        self.assertEqual(
            (await self.signed_post("/webhooks/payments", changed)).status_code, 409
        )
        changed = dict(original, status="failed")
        self.assertEqual(
            (await self.signed_post("/webhooks/payments", changed)).status_code, 409
        )
        async with self.db.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT p.tokens, p.status, l.delta FROM payments p "
                        "JOIN ledger l ON l.payment_id = p.payment_id "
                        "WHERE p.payment_id = :id"
                    ),
                    {"id": payment_id},
                )
            ).all()
        self.assertEqual(rows, [(7, "succeeded", 7)])
        self.assertEqual(
            (await self.client.get(f"/users/{self.user_id}/balance")).json()["balance"],
            7,
        )

    async def test_bigint_limits_reject_invalid_or_overflowing_credit(self) -> None:
        too_large = 2**63
        oversized_id = str(uuid4())
        oversized = await self.signed_post(
            "/webhooks/payments",
            {
                "payment_id": oversized_id,
                "user_id": self.user_id,
                "tokens": too_large,
                "status": "succeeded",
            },
        )
        self.assertEqual(oversized.status_code, 422, oversized.text)
        self.assertEqual(
            (
                await self.signed_post(
                    "/webhooks/payments",
                    {
                        "payment_id": str(uuid4()),
                        "user_id": too_large,
                        "tokens": 1,
                        "status": "succeeded",
                    },
                )
            ).status_code,
            422,
        )
        self.assertEqual(
            (
                await self.client.post(
                    "/generate",
                    json={
                        "user_id": too_large,
                        "model_slug": "image-fast",
                        "prompt": "invalid ID",
                        "ref_images": [],
                    },
                )
            ).status_code,
            422,
        )
        self.assertEqual(
            (await self.client.get(f"/users/{too_large}/balance")).status_code,
            422,
        )
        maximum = 2**63 - 1
        self.assertEqual(
            (
                await self.signed_post(
                    "/webhooks/payments",
                    {
                        "payment_id": str(uuid4()),
                        "user_id": self.user_id,
                        "tokens": maximum,
                        "status": "succeeded",
                    },
                )
            ).status_code,
            200,
        )
        overflow_id = str(uuid4())
        overflowing = await self.signed_post(
            "/webhooks/payments",
            {
                "payment_id": overflow_id,
                "user_id": self.user_id,
                "tokens": 1,
                "status": "succeeded",
            },
        )
        self.assertEqual(overflowing.status_code, 409, overflowing.text)
        async with self.db.connect() as conn:
            stored = (
                await conn.execute(
                    text("SELECT count(*) FROM payments WHERE payment_id = :id"),
                    {"id": overflow_id},
                )
            ).scalar_one()
        self.assertEqual(stored, 0)
        self.assertEqual(
            (await self.client.get(f"/users/{self.user_id}/balance")).json()["balance"],
            maximum,
        )

    async def test_concurrent_payments_cannot_overflow_balance(self) -> None:
        maximum = 2**63 - 1
        ids = [str(uuid4()), str(uuid4())]
        responses = await asyncio.gather(
            *(
                self.signed_post(
                    "/webhooks/payments",
                    {
                        "payment_id": payment_id,
                        "user_id": self.user_id,
                        "tokens": maximum,
                        "status": "succeeded",
                    },
                )
                for payment_id in ids
            )
        )
        self.assertEqual(
            sorted(response.status_code for response in responses), [200, 409]
        )
        async with self.db.connect() as conn:
            stored = (
                await conn.execute(
                    text(
                        "SELECT count(*), coalesce(sum(tokens), 0) "
                        "FROM payments WHERE payment_id IN (:first, :second)"
                    ),
                    {"first": ids[0], "second": ids[1]},
                )
            ).one()
        self.assertEqual(stored, (1, maximum))
        self.assertEqual(
            (await self.client.get(f"/users/{self.user_id}/balance")).json()["balance"],
            maximum,
        )

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

    async def test_parallel_conflicting_provider_outcomes_keep_one_terminal_effect(
        self,
    ) -> None:
        await self.credit(20)
        await self.configure("alpha", no_webhook=True)
        await self.configure("beta", no_webhook=True)
        for iteration in range(4):
            task_id = await self.generate()
            task = (await self.client.get(f"/tasks/{task_id}")).json()
            first = task["attempts"][0]
            complete = {
                "external_id": first["id"],
                "status": "completed",
                "result_url": f"https://example.invalid/race/{iteration}",
            }
            failed = {
                "external_id": first["id"],
                "status": "failed",
                "error_type": "technical",
            }
            events = [complete] * 10 + [failed] * 10
            random.Random(iteration).shuffle(events)
            responses = await asyncio.gather(
                *(
                    self.signed_post("/webhooks/providers/alpha", event)
                    for event in events
                )
            )
            self.assertTrue(
                all(response.status_code == 200 for response in responses),
                [response.text for response in responses],
            )
            task = (await self.client.get(f"/tasks/{task_id}")).json()
            if task["status"] == "running":
                self.assertEqual(len(task["attempts"]), 2)
                second = next(
                    row for row in task["attempts"] if row["provider"] == "beta"
                )
                response = await self.signed_post(
                    "/webhooks/providers/beta",
                    {
                        "external_id": second["id"],
                        "status": "failed",
                        "error_type": "technical",
                    },
                )
                self.assertEqual(response.status_code, 200, response.text)
                task = (await self.client.get(f"/tasks/{task_id}")).json()
            self.assertIn(task["status"], ("completed", "failed"))
            async with self.db.connect() as conn:
                entries = (
                    await conn.execute(
                        text("SELECT reason, delta FROM ledger WHERE task_id = :id"),
                        {"id": task_id},
                    )
                ).all()
            self.assertEqual(entries.count(("charge", -2)), 1)
            self.assertEqual(
                entries.count(("refund", 2)), int(task["status"] == "failed")
            )
            self.assertFalse(task["result_url"] and ("refund", 2) in entries)

    async def test_reference_limit_applies_to_fallback_leg(self) -> None:
        await self.credit(25)
        await self.configure("beta", no_webhook=True)
        response = await self.client.post(
            "/generate",
            json={
                "user_id": self.user_id,
                "model_slug": "video-long",
                "prompt": "two references",
                "ref_images": ["a", "b"],
            },
        )
        self.assertEqual(response.status_code, 202, response.text)
        task_id = response.json()["task_id"]
        task = (await self.client.get(f"/tasks/{task_id}")).json()
        self.assertEqual(len(task["attempts"]), 1)
        self.assertEqual(task["attempts"][0]["provider"], "beta")
        result = await self.signed_post(
            "/webhooks/providers/beta",
            {
                "external_id": task["attempts"][0]["id"],
                "status": "failed",
                "error_type": "technical",
            },
        )
        self.assertEqual(result.status_code, 200, result.text)
        task = (await self.client.get(f"/tasks/{task_id}")).json()
        self.assertEqual(task["status"], "failed")
        self.assertEqual(len(task["attempts"]), 1)
        self.assertEqual(
            (await self.client.get(f"/users/{self.user_id}/balance")).json()["balance"],
            25,
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

    async def test_watchdog_dispatches_committed_pending_attempt(self) -> None:
        await self.credit()
        await self.configure("alpha", delay_seconds=0)
        task_id, attempt_id = uuid4(), uuid4()
        async with self.db.begin() as conn:
            leg_id = (
                await conn.execute(
                    text(
                        "SELECT id FROM legs "
                        "WHERE model_slug = 'image-fast' AND provider = 'alpha'"
                    )
                )
            ).scalar_one()
            await conn.execute(
                text("UPDATE users SET balance = balance - 2 WHERE id = :id"),
                {"id": self.user_id},
            )
            await conn.execute(
                text(
                    "INSERT INTO tasks "
                    "(id, user_id, model_slug, prompt, price, status) "
                    "VALUES (:id, :user_id, 'image-fast', 'recovery', 2, 'running')"
                ),
                {"id": task_id, "user_id": self.user_id},
            )
            await conn.execute(
                text(
                    "INSERT INTO ledger (user_id, delta, reason, task_id) "
                    "VALUES (:user_id, -2, 'charge', :task_id)"
                ),
                {"user_id": self.user_id, "task_id": task_id},
            )
            await conn.execute(
                text(
                    "INSERT INTO attempts "
                    "(id, task_id, leg_id, provider, external_id, status, deadline_at) "
                    "VALUES (:id, :task_id, :leg_id, 'alpha', :external_id, "
                    "'pending', now())"
                ),
                {
                    "id": attempt_id,
                    "task_id": task_id,
                    "leg_id": leg_id,
                    "external_id": str(attempt_id),
                },
            )
            await conn.execute(
                text(
                    "UPDATE tasks SET current_attempt_id = :attempt_id WHERE id = :task_id"
                ),
                {"attempt_id": attempt_id, "task_id": task_id},
            )
        task = await self.wait_task(
            str(task_id), lambda row: row["status"] == "completed"
        )
        self.assertEqual(len(task["attempts"]), 1)
        self.assertEqual(task["attempts"][0]["status"], "succeeded")
        async with self.db.connect() as conn:
            entries = (
                await conn.execute(
                    text("SELECT reason, delta FROM ledger WHERE task_id = :id"),
                    {"id": task_id},
                )
            ).all()
        self.assertEqual(entries, [("charge", -2)])
        self.assertEqual(
            (await self.client.get(f"/users/{self.user_id}/balance")).json()["balance"],
            98,
        )

    async def test_simultaneous_fake_provider_callbacks_keep_one_charge(self) -> None:
        await self.credit()
        await self.configure("alpha", duplicate_webhooks=True)
        task_id = await self.generate()
        task = await self.wait_task(task_id, lambda x: x["status"] == "completed")
        self.assertEqual(len(task["attempts"]), 1)
        await asyncio.sleep(0.2)
        task = (await self.client.get(f"/tasks/{task_id}")).json()
        self.assertIsNone(task["attempts"][0]["late_result_url"])
        async with self.db.connect() as conn:
            rows = (
                await conn.execute(
                    text("SELECT reason, delta FROM ledger WHERE task_id = :id"),
                    {"id": task_id},
                )
            ).all()
        self.assertEqual(rows, [("charge", -2)])

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

    async def test_late_first_leg_completion_cannot_replace_second_leg_result(
        self,
    ) -> None:
        await self.credit()
        await self.configure("alpha", no_webhook=True)
        await self.configure("beta", no_webhook=True)
        task_id = await self.generate()
        task = (await self.client.get(f"/tasks/{task_id}")).json()
        first = task["attempts"][0]
        response = await self.signed_post(
            "/webhooks/providers/alpha",
            {
                "external_id": first["id"],
                "status": "failed",
                "error_type": "technical",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        task = (await self.client.get(f"/tasks/{task_id}")).json()
        self.assertEqual(len(task["attempts"]), 2)
        second = next(row for row in task["attempts"] if row["provider"] == "beta")
        late_url = "https://example.invalid/late-first"
        response = await self.signed_post(
            "/webhooks/providers/alpha",
            {
                "external_id": first["id"],
                "status": "completed",
                "result_url": late_url,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        final_url = "https://example.invalid/second"
        response = await self.signed_post(
            "/webhooks/providers/beta",
            {
                "external_id": second["id"],
                "status": "completed",
                "result_url": final_url,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        task = (await self.client.get(f"/tasks/{task_id}")).json()
        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["result_url"], final_url)
        self.assertEqual(task["attempts"][0]["late_result_url"], late_url)
        async with self.db.connect() as conn:
            entries = (
                await conn.execute(
                    text("SELECT reason, delta FROM ledger WHERE task_id = :id"),
                    {"id": task_id},
                )
            ).all()
        self.assertEqual(entries, [("charge", -2)])

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
        orders = await asyncio.gather(
            *(
                self.client.post(
                    "/generate",
                    json={
                        "user_id": self.user_id,
                        "model_slug": "image-fast",
                        "prompt": "test",
                        "ref_images": [],
                    },
                )
                for _ in range(4)
            )
        )
        self.assertEqual(
            sorted(order.status_code for order in orders), [202, 202, 202, 429]
        )
        task_ids = [
            order.json()["task_id"] for order in orders if order.status_code == 202
        ]
        self.assertEqual(len(set(task_ids)), 3)
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

    async def test_moderation_skips_later_flagged_leg_after_technical_failure(
        self,
    ) -> None:
        slug = f"moderation-chain-{uuid4()}"
        async with self.db.begin() as conn:
            await conn.execute(
                text("INSERT INTO models (slug, price) VALUES (:slug, 2)"),
                {"slug": slug},
            )
            await conn.execute(
                text(
                    "INSERT INTO legs "
                    "(model_slug, provider, priority, max_ref_images, skip_on_moderation) "
                    "VALUES (:slug, 'alpha', 1, 0, false), "
                    "(:slug, 'beta', 2, 0, false), "
                    "(:slug, 'alpha', 3, 0, true)"
                ),
                {"slug": slug},
            )
        await self.credit()
        await self.configure("alpha", failure_rate=1, failure_type="moderation")
        await self.configure("beta", failure_rate=1, failure_type="technical")
        task_id = await self.generate(slug)
        task = await self.wait_task(task_id, lambda x: x["status"] == "failed")
        self.assertEqual(len(task["attempts"]), 2)
        self.assertEqual([a["provider"] for a in task["attempts"]], ["alpha", "beta"])
        self.assertEqual(
            [a["error_type"] for a in task["attempts"]], ["moderation", "technical"]
        )
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
        invalid_payment = await self.signed_post(
            "/webhooks/payments",
            {
                "payment_id": str(uuid4()),
                "user_id": self.user_id,
                "status": "succeeded",
            },
        )
        self.assertEqual(invalid_payment.status_code, 422)
        invalid_provider = await self.signed_post(
            "/webhooks/providers/alpha",
            {"external_id": str(uuid4()), "status": "completed"},
        )
        self.assertEqual(invalid_provider.status_code, 422)
        await self.configure("alpha", failure_rate=1, failure_type="payload")
        task_id = await self.generate()
        task = await self.wait_task(task_id, lambda x: x["status"] == "failed")
        self.assertEqual(len(task["attempts"]), 1)
        self.assertEqual(task["error_type"], "payload")
        self.assertEqual(
            (await self.client.get(f"/users/{self.user_id}/balance")).json()["balance"],
            100,
        )
