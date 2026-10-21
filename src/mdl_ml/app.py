import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal
from uuid import UUID

from fastapi import FastAPI, HTTPException, Path, Request, Response
from pydantic import BaseModel, ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from mdl_ml.config import Settings
from mdl_ml.database import create_engine
from mdl_ml.models import Attempt, LedgerEntry, Leg, Task, User
from mdl_ml.schemas import (
    BIGINT_MAX,
    AttemptRow,
    BalanceResponse,
    GenerateRequest,
    GenerateResponse,
    LedgerRow,
    PaymentWebhook,
    ProviderWebhook,
    TaskResponse,
)
from mdl_ml.service import (
    create_generation,
    dispatch_attempt,
    handle_provider_webhook,
    record_payment,
    verify_signature,
    watchdog_loop,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings()
    engine = create_engine(settings.database_url)
    app.state.database = engine
    app.state.settings = settings
    watcher = asyncio.create_task(watchdog_loop(engine, settings))
    try:
        yield
    finally:
        watcher.cancel()
        try:
            await watcher
        except asyncio.CancelledError:
            pass
        await engine.dispose()


app = FastAPI(title="MDL ML", lifespan=lifespan)


class HealthResponse(BaseModel):
    status: Literal["ok", "error"]
    application: Literal["ok"] = "ok"
    database: Literal["ok", "error"]


@app.get(
    "/health", response_model=HealthResponse, responses={503: {"model": HealthResponse}}
)
async def health(request: Request, response: Response) -> HealthResponse:
    try:
        async with asyncio.timeout(5):
            async with request.app.state.database.connect() as connection:
                await connection.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError, TimeoutError):
        response.status_code = 503
        return HealthResponse(status="error", database="error")
    return HealthResponse(status="ok", database="ok")


@app.post("/generate", response_model=GenerateResponse, status_code=202)
async def generate(data: GenerateRequest, request: Request) -> GenerateResponse:
    task_id = await create_generation(request.app.state.database, data)
    async with AsyncSession(request.app.state.database) as conn:
        attempt_id = (
            await conn.execute(
                select(Task.current_attempt_id).where(Task.id == task_id)
            )
        ).scalar_one()
    if attempt_id is None:
        raise RuntimeError("New task has no attempt")
    await dispatch_attempt(
        request.app.state.database, request.app.state.settings, attempt_id
    )
    return GenerateResponse(task_id=task_id)


@app.post("/webhooks/payments")
async def payment_webhook(request: Request) -> dict[str, str]:
    body = await request.body()
    verify_signature(
        body,
        request.headers.get("X-Signature"),
        request.app.state.settings.webhook_secret,
    )
    try:
        event = PaymentWebhook.model_validate_json(body)
    except ValidationError as exc:
        raise HTTPException(422, "Invalid payment webhook") from exc
    await record_payment(request.app.state.database, event)
    return {"status": "accepted"}


@app.post("/webhooks/providers/{name}")
async def provider_webhook(name: str, request: Request) -> dict[str, str]:
    if name not in ("alpha", "beta"):
        raise HTTPException(404, "Unknown provider")
    body = await request.body()
    verify_signature(
        body,
        request.headers.get("X-Signature"),
        request.app.state.settings.webhook_secret,
    )
    try:
        event = ProviderWebhook.model_validate_json(body)
    except ValidationError as exc:
        raise HTTPException(422, "Invalid provider webhook") from exc
    await handle_provider_webhook(
        request.app.state.database, request.app.state.settings, name, event
    )
    return {"status": "accepted"}


@app.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(task_id: UUID, request: Request) -> TaskResponse:
    async with AsyncSession(request.app.state.database) as conn:
        # Keep the task and its attempt history on one committed transition.
        task = (
            await conn.execute(
                select(Task).where(Task.id == task_id).with_for_update(read=True)
            )
        ).scalar_one_or_none()
        if task is None:
            raise HTTPException(404, "Unknown task")
        attempts = (
            (
                await conn.execute(
                    select(Attempt)
                    .join(Leg, Attempt.leg_id == Leg.id)
                    .where(Attempt.task_id == task_id)
                    .order_by(Leg.priority)
                )
            )
            .scalars()
            .all()
        )
    current = next((a for a in attempts if a.id == task.current_attempt_id), None)
    return TaskResponse(
        id=task.id,
        user_id=task.user_id,
        model_slug=task.model_slug,
        price=task.price,
        status=task.status,
        current_leg_id=current.leg_id if current else None,
        current_provider=current.provider if current else None,
        current_attempt_id=task.current_attempt_id,
        result_url=task.result_url,
        error_type=task.error_type,
        created_at=task.created_at,
        updated_at=task.updated_at,
        attempts=[AttemptRow.model_validate(a, from_attributes=True) for a in attempts],
    )


@app.get("/users/{user_id}/balance", response_model=BalanceResponse)
async def get_balance(
    request: Request, user_id: int = Path(gt=0, le=BIGINT_MAX)
) -> BalanceResponse:
    async with AsyncSession(request.app.state.database) as conn:
        # A shared row lock keeps the balance and ledger page consistent.
        user = (
            await conn.execute(
                select(User).where(User.id == user_id).with_for_update(read=True)
            )
        ).scalar_one_or_none()
        if user is None:
            raise HTTPException(404, "Unknown user")
        rows = (
            (
                await conn.execute(
                    select(LedgerEntry)
                    .where(LedgerEntry.user_id == user_id)
                    .order_by(LedgerEntry.id.desc())
                    .limit(20)
                )
            )
            .scalars()
            .all()
        )
    return BalanceResponse(
        user_id=user_id,
        balance=user.balance,
        ledger=[LedgerRow.model_validate(row, from_attributes=True) for row in rows],
    )
