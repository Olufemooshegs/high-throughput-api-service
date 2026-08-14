import os

from fastapi import FastAPI, Request, Response

from app.backpressure import (
    BackpressureLimiter,
    build_backpressure_response,
    optional_test_delay,
    read_int_setting,
)
from app.db import database_lifespan
from app.routes.health import router as health_router
from app.routes.messages import router as messages_router


INSTANCE_ID = os.getenv("INSTANCE_ID", "local")
MAX_CONCURRENT_REQUESTS = read_int_setting("BACKPRESSURE_MAX_CONCURRENT_REQUESTS", 50)
RETRY_AFTER_SECONDS = read_int_setting("BACKPRESSURE_RETRY_AFTER_SECONDS", 1)

app = FastAPI(title="High-Throughput API Service", lifespan=database_lifespan)
backpressure_limiter = BackpressureLimiter(MAX_CONCURRENT_REQUESTS)


@app.middleware("http")
async def apply_backpressure(request: Request, call_next) -> Response:
    async with backpressure_limiter.slot() as acquired:
        if not acquired:
            response = build_backpressure_response(
                limit=MAX_CONCURRENT_REQUESTS,
                retry_after_seconds=RETRY_AFTER_SECONDS,
            )
            response.headers["X-Instance-ID"] = INSTANCE_ID
            return response

        await optional_test_delay()
        return await call_next(request)


@app.middleware("http")
async def add_instance_id_header(request: Request, call_next) -> Response:
    response = await call_next(request)
    response.headers["X-Instance-ID"] = INSTANCE_ID
    return response


app.include_router(health_router)
app.include_router(messages_router)
