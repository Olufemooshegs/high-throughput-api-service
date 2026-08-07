from datetime import datetime

import asyncpg
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field


router = APIRouter(prefix="/messages", tags=["messages"])


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=500)


class Message(BaseModel):
    id: int
    content: str
    created_at: datetime


def get_pool(request: Request) -> asyncpg.Pool:
    return request.app.state.db_pool


@router.post("", response_model=Message, status_code=status.HTTP_201_CREATED)
async def create_message(payload: MessageCreate, request: Request) -> dict[str, object]:
    pool = get_pool(request)

    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            """
            INSERT INTO messages (content)
            VALUES ($1)
            RETURNING id, content, created_at
            """,
            payload.content,
        )

    return dict(row)


@router.get("/{message_id}", response_model=Message)
async def get_message(message_id: int, request: Request) -> dict[str, object]:
    pool = get_pool(request)

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

    return dict(row)
