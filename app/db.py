import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import asyncpg
import redis.asyncio as redis
from fastapi import FastAPI


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://app:app@localhost:5432/high_throughput",
)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
POOL_MIN_SIZE = 2
POOL_MAX_SIZE = 10


@asynccontextmanager
async def database_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create shared database and cache clients for this API process."""
    app.state.db_pool = await asyncpg.create_pool(
        dsn=DATABASE_URL,
        min_size=POOL_MIN_SIZE,
        max_size=POOL_MAX_SIZE,
    )
    app.state.redis = redis.from_url(REDIS_URL, decode_responses=True)
    await app.state.redis.ping()

    async with app.state.db_pool.acquire() as connection:
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id BIGSERIAL PRIMARY KEY,
                content TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )

    try:
        yield
    finally:
        await app.state.redis.aclose()
        await app.state.db_pool.close()
