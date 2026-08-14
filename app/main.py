import os

from fastapi import FastAPI, Request, Response

from app.db import database_lifespan
from app.routes.health import router as health_router
from app.routes.messages import router as messages_router


INSTANCE_ID = os.getenv("INSTANCE_ID", "local")

app = FastAPI(title="High-Throughput API Service", lifespan=database_lifespan)


@app.middleware("http")
async def add_instance_id_header(request: Request, call_next) -> Response:
    response = await call_next(request)
    response.headers["X-Instance-ID"] = INSTANCE_ID
    return response


app.include_router(health_router)
app.include_router(messages_router)
