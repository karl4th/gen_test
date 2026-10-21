import asyncio
from contextlib import asynccontextmanager
from typing import Literal

from asyncpg import PostgresError
from fastapi import FastAPI, Request, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from mdl_ml.config import Settings
from mdl_ml.database import create_engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = create_engine(Settings().database_url)
    app.state.database = engine
    try:
        yield
    finally:
        await engine.dispose()


app = FastAPI(title="MDL ML", lifespan=lifespan)


class HealthResponse(BaseModel):
    status: Literal["ok", "error"]
    application: Literal["ok"] = "ok"
    database: Literal["ok", "error"]


@app.get("/health", response_model=HealthResponse, responses={503: {"model": HealthResponse}})
async def health(request: Request, response: Response) -> HealthResponse:
    try:
        async with asyncio.timeout(5):
            async with request.app.state.database.connect() as connection:
                await connection.execute(text("SELECT 1"))
    except (SQLAlchemyError, PostgresError, OSError, TimeoutError):
        response.status_code = 503
        return HealthResponse(status="error", database="error")
    return HealthResponse(status="ok", database="ok")
