import json
from datetime import datetime

import asyncpg
from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from redis.asyncio import Redis

import logging
from redis.exceptions import RedisError

router = APIRouter(prefix="/messages", tags=["messages"])
CACHE_TTL_SECONDS = 60


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=500)


class Message(BaseModel):
    id: int
    content: str
    created_at: datetime


def get_pool(request: Request) -> asyncpg.Pool:
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="Database pool not initialized")
    return pool


def get_cache(request: Request) -> Redis:
    cache = getattr(request.app.state, "redis", None)
    if cache is None:
        raise HTTPException(status_code=503, detail="Redis cache not initialized")
    return cache


def cache_key(message_id: int) -> str:
    return f"messages:{message_id}"


def serialize_message(row: asyncpg.Record) -> dict[str, object]:
    return {
        "id": row["id"],
        "content": row["content"],
        "created_at": row["created_at"].isoformat(),
    }
logger = logging.getLogger(__name__)


async def safe_cache_get(cache: Redis, key: str) -> str | None:
    try:
        return await cache.get(key)
    except RedisError:
        logger.warning("Redis GET failed for key=%s, falling back to DB", key)
        return None


async def safe_cache_set(cache: Redis, key: str, value: str, ttl: int) -> None:
    try:
        await cache.set(key, value, ex=ttl)
    except RedisError:
        logger.warning("Redis SET failed for key=%s, continuing without cache", key)

@router.post("", response_model=Message, status_code=status.HTTP_201_CREATED)
async def create_message(payload: MessageCreate, request: Request) -> dict[str, object]:
    pool = get_pool(request)
    cache = get_cache(request)

    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            """
            INSERT INTO messages (content)
            VALUES ($1)
            RETURNING id, content, created_at
            """,
            payload.content,
        )

    message = serialize_message(row)
    await safe_cache_set(cache, cache_key(message["id"]), json.dumps(message), CACHE_TTL_SECONDS)

    return message


@router.get("/{message_id}", response_model=Message)
async def get_message(message_id: int, request: Request, response: Response) -> dict[str, object]:
    pool = get_pool(request)
    cache = get_cache(request)

    cached_message = await cache.get(cache_key(message_id))
    if cached_message is not None:
        response.headers["X-Cache"] = "HIT"
        return json.loads(cached_message)

    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            """
            SELECT id, content, created_at
            FROM messages
            WHERE id = $1
            """,
            message_id,
        )

    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")

    message = serialize_message(row)
    await safe_cache_set(cache, cache_key(message_id), json.dumps(message), CACHE_TTL_SECONDS)
    response.headers["X-Cache"] = "MISS"

    return message
