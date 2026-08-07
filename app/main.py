from fastapi import FastAPI

from app.db import database_lifespan
from app.routes.health import router as health_router
from app.routes.messages import router as messages_router


app = FastAPI(title="High-Throughput API Service", lifespan=database_lifespan)
app.include_router(health_router)
app.include_router(messages_router)
