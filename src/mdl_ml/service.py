import asyncio
import hmac
import logging
from datetime import timedelta
from hashlib import sha256
from uuid import UUID, uuid4

import httpx
from fastapi import HTTPException
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from mdl_ml.config import Settings
from mdl_ml.domain.statuses import (
    AttemptStatus,
    PaymentStatus,
    ProviderErrorType,
    TaskStatus,
)
from mdl_ml.models import Attempt, LedgerEntry, Leg, Model, Payment, Task, User
from mdl_ml.schemas import GenerateRequest, PaymentWebhook, ProviderWebhook

log = logging.getLogger(__name__)
ACTIVE = (TaskStatus.PENDING, TaskStatus.RUNNING)


def verify_signature(body: bytes, signature: str | None, secret: str) -> None:
    expected = hmac.new(secret.encode(), body, sha256).hexdigest()
    if signature is None or not hmac.compare_digest(signature, expected):
        raise HTTPException(401, "Invalid signature")


async def record_payment(engine: AsyncEngine, event: PaymentWebhook) -> None:
    async with AsyncSession(engine) as conn, conn.begin():
        if await conn.scalar(select(User.id).where(User.id == event.user_id)) is None:
            raise HTTPException(404, "Unknown user")
        inserted = await conn.execute(
            pg_insert(Payment)
            .values(
                payment_id=event.payment_id,
                user_id=event.user_id,
                tokens=event.tokens,
                status=event.status,
                received_at=func.now(),
            )
            .on_conflict_do_nothing()
            .returning(Payment.payment_id)
        )
        if inserted.scalar_one_or_none() is None:
            existing = (
                await conn.execute(
                    select(Payment).where(Payment.payment_id == event.payment_id)
                )
            ).scalar_one()
            if (existing.user_id, existing.tokens, existing.status) != (
                event.user_id,
                event.tokens,
                event.status,
            ):
                raise HTTPException(409, "Payment ID has different data")
            return
        if event.status == PaymentStatus.SUCCEEDED:
            await conn.execute(
                insert(LedgerEntry).values(
                    user_id=event.user_id,
                    delta=event.tokens,
                    reason="payment",
                    payment_id=event.payment_id,
                )
            )
            await conn.execute(
                update(User)
                .where(User.id == event.user_id)
                .values(balance=User.balance + event.tokens)
            )


async def create_generation(engine: AsyncEngine, data: GenerateRequest) -> UUID:
    async with AsyncSession(engine) as conn, conn.begin():
        model = (
            await conn.execute(select(Model).where(Model.slug == data.model_slug))
        ).scalar_one_or_none()
        if model is None:
            raise HTTPException(404, "Unknown model")
        leg = (
            await conn.execute(
                select(Leg)
                .where(
                    Leg.model_slug == model.slug,
                    Leg.max_ref_images >= len(data.ref_images),
                )
                .order_by(Leg.priority, Leg.id)
                .limit(1)
            )
        ).scalar_one_or_none()
        if leg is None:
            raise HTTPException(400, "No leg accepts this many reference images")
        user = (
            await conn.execute(
                select(User).where(User.id == data.user_id).with_for_update()
            )
        ).scalar_one_or_none()
        if user is None:
            raise HTTPException(404, "Unknown user")
        active = (
            await conn.execute(
                select(func.count())
                .select_from(Task)
                .where(Task.user_id == data.user_id, Task.status.in_(ACTIVE))
            )
        ).scalar_one()
        if active >= 3:
            raise HTTPException(429, "Three active tasks already exist")
        debited = await conn.execute(
            update(User)
            .where(User.id == data.user_id, User.balance >= model.price)
            .values(balance=User.balance - model.price)
            .returning(User.id)
        )
        if debited.scalar_one_or_none() is None:
            raise HTTPException(402, "Insufficient tokens")
        task_id, attempt_id = uuid4(), uuid4()
        await conn.execute(
            insert(Task).values(
                id=task_id,
                user_id=data.user_id,
                model_slug=model.slug,
                prompt=data.prompt,
                ref_images=data.ref_images,
                price=model.price,
                status=TaskStatus.RUNNING,
            )
        )
        await conn.execute(
            insert(LedgerEntry).values(
                user_id=data.user_id,
                delta=-model.price,
                reason="charge",
                task_id=task_id,
            )
        )
        await conn.execute(
            insert(Attempt).values(
                id=attempt_id,
                task_id=task_id,
                leg_id=leg.id,
                provider=leg.provider,
                external_id=str(attempt_id),
                status=AttemptStatus.PENDING,
                deadline_at=func.now(),
            )
        )
        await conn.execute(
            update(Task).where(Task.id == task_id).values(current_attempt_id=attempt_id)
        )
        return task_id


async def dispatch_attempt(
    engine: AsyncEngine, settings: Settings, attempt_id: UUID
) -> None:
    async with AsyncSession(engine) as conn, conn.begin():
        claimed = await conn.execute(
            update(Attempt)
            .where(
                Attempt.id == attempt_id,
                Attempt.status == AttemptStatus.PENDING,
                Attempt.task_id.in_(
                    select(Task.id).where(
                        Task.status == TaskStatus.RUNNING,
                        Task.current_attempt_id == attempt_id,
                    )
                ),
            )
            .values(
                status=AttemptStatus.SENT,
                sent_at=func.now(),
                deadline_at=func.now() + timedelta(seconds=settings.provider_timeout),
            )
            .returning(Attempt.task_id, Attempt.provider, Attempt.external_id)
        )
        row = claimed.one_or_none()
        if row is None:
            return
        task = (
            await conn.execute(
                select(Task.prompt, Task.ref_images).where(Task.id == row.task_id)
            )
        ).one()
    payload = {
        "external_id": row.external_id,
        "prompt": task.prompt,
        "ref_images": task.ref_images,
    }
    try:
        async with httpx.AsyncClient(
            timeout=settings.provider_http_timeout_seconds, trust_env=False
        ) as client:
            response = await client.post(
                f"{settings.provider_url(row.provider)}/tasks", json=payload
            )
            response.raise_for_status()
            if (
                response.status_code != 202
                or response.json().get("external_id") != row.external_id
            ):
                raise ValueError("Invalid provider acknowledgment")
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("Provider dispatch failed for %s: %s", attempt_id, exc)
        next_id = await settle_attempt(
            engine, attempt_id, "failed", ProviderErrorType.TECHNICAL, None
        )
        if next_id is not None:
            await dispatch_attempt(engine, settings, next_id)


async def settle_attempt(
    engine: AsyncEngine,
    attempt_id: UUID,
    outcome: str,
    error_type: ProviderErrorType | None,
    result_url: str | None,
    *,
    timed_out: bool = False,
) -> UUID | None:
    async with AsyncSession(engine) as conn, conn.begin():
        attempt = (
            await conn.execute(select(Attempt).where(Attempt.id == attempt_id))
        ).scalar_one_or_none()
        if attempt is None:
            raise HTTPException(404, "Unknown attempt")
        task = (
            await conn.execute(
                select(Task).where(Task.id == attempt.task_id).with_for_update()
            )
        ).scalar_one()
        await conn.refresh(attempt)
        if (
            task.status != TaskStatus.RUNNING
            or task.current_attempt_id != attempt_id
            or attempt.status not in (AttemptStatus.PENDING, AttemptStatus.SENT)
        ):
            if (
                outcome == "completed"
                and result_url
                and attempt.status in (AttemptStatus.FAILED, AttemptStatus.TIMED_OUT)
                and attempt.late_result_url is None
            ):
                await conn.execute(
                    update(Attempt)
                    .where(Attempt.id == attempt_id, Attempt.late_result_url.is_(None))
                    .values(late_result_url=result_url)
                )
            return None
        if outcome == "completed":
            await conn.execute(
                update(Attempt)
                .where(
                    Attempt.id == attempt_id,
                    Attempt.status.in_((AttemptStatus.PENDING, AttemptStatus.SENT)),
                )
                .values(status=AttemptStatus.SUCCEEDED, finished_at=func.now())
            )
            await conn.execute(
                update(Task)
                .where(
                    Task.id == task.id,
                    Task.status == TaskStatus.RUNNING,
                    Task.current_attempt_id == attempt_id,
                )
                .values(
                    status=TaskStatus.COMPLETED,
                    result_url=result_url,
                    updated_at=func.now(),
                )
            )
            return None
        new_status = AttemptStatus.TIMED_OUT if timed_out else AttemptStatus.FAILED
        await conn.execute(
            update(Attempt)
            .where(
                Attempt.id == attempt_id,
                Attempt.status.in_((AttemptStatus.PENDING, AttemptStatus.SENT)),
            )
            .values(status=new_status, error_type=error_type, finished_at=func.now())
        )
        next_leg = None
        if error_type != ProviderErrorType.PAYLOAD:
            current_leg = (
                await conn.execute(select(Leg).where(Leg.id == attempt.leg_id))
            ).scalar_one()
            condition = [
                Leg.model_slug == task.model_slug,
                Leg.priority > current_leg.priority,
                Leg.max_ref_images >= func.jsonb_array_length(Task.ref_images),
                Task.id == task.id,
            ]
            moderation_seen = (
                await conn.execute(
                    select(func.count())
                    .select_from(Attempt)
                    .where(
                        Attempt.task_id == task.id,
                        Attempt.error_type == ProviderErrorType.MODERATION,
                    )
                )
            ).scalar_one() > 0
            if moderation_seen:
                condition.append(Leg.skip_on_moderation.is_(False))
            next_leg = (
                await conn.execute(
                    select(Leg)
                    .join(Task, Leg.model_slug == Task.model_slug)
                    .where(*condition)
                    .order_by(Leg.priority, Leg.id)
                    .limit(1)
                )
            ).scalar_one_or_none()
        if next_leg is not None:
            next_id = uuid4()
            await conn.execute(
                insert(Attempt).values(
                    id=next_id,
                    task_id=task.id,
                    leg_id=next_leg.id,
                    provider=next_leg.provider,
                    external_id=str(next_id),
                    status=AttemptStatus.PENDING,
                    deadline_at=func.now(),
                )
            )
            await conn.execute(
                update(Task)
                .where(
                    Task.id == task.id,
                    Task.status == TaskStatus.RUNNING,
                    Task.current_attempt_id == attempt_id,
                )
                .values(current_attempt_id=next_id, updated_at=func.now())
            )
            return next_id
        await conn.execute(
            update(Task)
            .where(
                Task.id == task.id,
                Task.status == TaskStatus.RUNNING,
                Task.current_attempt_id == attempt_id,
            )
            .values(
                status=TaskStatus.FAILED, error_type=error_type, updated_at=func.now()
            )
        )
        refunded = await conn.execute(
            pg_insert(LedgerEntry)
            .values(
                user_id=task.user_id,
                delta=task.price,
                reason="refund",
                task_id=task.id,
            )
            .on_conflict_do_nothing(
                index_elements=[LedgerEntry.task_id],
                index_where=text("reason = 'refund'"),
            )
            .returning(LedgerEntry.id)
        )
        if refunded.scalar_one_or_none() is not None:
            await conn.execute(
                update(User)
                .where(User.id == task.user_id)
                .values(balance=User.balance + task.price)
            )
        return None


async def handle_provider_webhook(
    engine: AsyncEngine, settings: Settings, name: str, event: ProviderWebhook
) -> None:
    async with AsyncSession(engine) as conn:
        attempt = (
            await conn.execute(
                select(Attempt.id).where(
                    Attempt.provider == name, Attempt.external_id == event.external_id
                )
            )
        ).scalar_one_or_none()
    if attempt is None:
        raise HTTPException(404, "Unknown attempt")
    next_id = await settle_attempt(
        engine, attempt, event.status, event.error_type, event.result_url
    )
    if next_id is not None:
        await dispatch_attempt(engine, settings, next_id)


async def watchdog_loop(engine: AsyncEngine, settings: Settings) -> None:
    while True:
        try:
            async with AsyncSession(engine) as conn:
                pending = (
                    (
                        await conn.execute(
                            select(Attempt.id)
                            .where(
                                Attempt.status == AttemptStatus.PENDING,
                                Attempt.deadline_at <= func.now(),
                            )
                            .limit(100)
                        )
                    )
                    .scalars()
                    .all()
                )
                expired = (
                    (
                        await conn.execute(
                            select(Attempt.id)
                            .where(
                                Attempt.status == AttemptStatus.SENT,
                                Attempt.deadline_at <= func.now(),
                            )
                            .limit(100)
                        )
                    )
                    .scalars()
                    .all()
                )
            for attempt_id in pending:
                await dispatch_attempt(engine, settings, attempt_id)
            for attempt_id in expired:
                next_id = await settle_attempt(
                    engine,
                    attempt_id,
                    "failed",
                    ProviderErrorType.TECHNICAL,
                    None,
                    timed_out=True,
                )
                if next_id is not None:
                    await dispatch_attempt(engine, settings, next_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Watchdog iteration failed")
        await asyncio.sleep(settings.watchdog_interval_seconds)
