import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import Request, status
from starlette.responses import JSONResponse


logger = logging.getLogger(__name__)

DEFAULT_MAX_CONCURRENT_REQUESTS = 50
DEFAULT_RETRY_AFTER_SECONDS = 1


def read_int_setting(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default

    try:
        return int(value)
    except ValueError:
        logger.warning("Invalid integer for %s=%r, using %s", name, value, default)
        return default


def read_float_setting(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default

    try:
        return float(value)
    except ValueError:
        logger.warning("Invalid float for %s=%r, using %s", name, value, default)
        return default


class BackpressureLimiter:
    def __init__(self, max_concurrent_requests: int) -> None:
        self.max_concurrent_requests = max_concurrent_requests
        self._active_requests = 0
        self._lock = asyncio.Lock()

    async def try_acquire(self) -> bool:
        async with self._lock:
            if self._active_requests >= self.max_concurrent_requests:
                return False

            self._active_requests += 1
            return True

    async def release(self) -> None:
        async with self._lock:
            self._active_requests -= 1

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[bool]:
        acquired = await self.try_acquire()
        try:
            yield acquired
        finally:
            if acquired:
                await self.release()


def build_backpressure_response(
    limit: int,
    retry_after_seconds: int = DEFAULT_RETRY_AFTER_SECONDS,
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "detail": {
                "error": "Server overloaded",
                "limit": limit,
                "reason": "Too many requests are already being processed by this API instance",
            }
        },
        headers={"Retry-After": str(retry_after_seconds)},
    )


async def optional_test_delay() -> None:
    delay_seconds = read_float_setting("BACKPRESSURE_TEST_DELAY_SECONDS", 0.0)
    if delay_seconds > 0:
        await asyncio.sleep(delay_seconds)
