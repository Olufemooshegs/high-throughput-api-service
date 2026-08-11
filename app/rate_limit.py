import logging
import time
import uuid

from fastapi import HTTPException, Request, status
from redis.asyncio import Redis
from redis.exceptions import RedisError


logger = logging.getLogger(__name__)

RATE_LIMIT_MAX_REQUESTS = 20
RATE_LIMIT_WINDOW_SECONDS = 10
SLIDING_WINDOW_SCRIPT = """
redis.call("ZREMRANGEBYSCORE", KEYS[1], 0, ARGV[1])
local request_count = redis.call("ZCARD", KEYS[1])

if request_count >= tonumber(ARGV[4]) then
    return {0, request_count}
end

redis.call("ZADD", KEYS[1], ARGV[2], ARGV[3])
redis.call("EXPIRE", KEYS[1], ARGV[5])
return {1, request_count + 1}
"""


def get_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()

    if request.client is None:
        return "unknown"

    return request.client.host


def rate_limit_key(client_ip: str, route: str) -> str:
    return f"rate_limit:{client_ip}:{route}"


async def enforce_rate_limit(
    cache: Redis,
    request: Request,
    route: str,
    limit: int = RATE_LIMIT_MAX_REQUESTS,
    window_seconds: int = RATE_LIMIT_WINDOW_SECONDS,
) -> None:
    client_ip = get_client_ip(request)
    key = rate_limit_key(client_ip, route)
    now = time.time()
    window_start = now - window_seconds
    member = f"{now}:{uuid.uuid4().hex}"

    try:
        allowed, request_count = await cache.eval(
            SLIDING_WINDOW_SCRIPT,
            1,
            key,
            window_start,
            now,
            member,
            limit,
            window_seconds,
        )

        if int(allowed) == 0:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": "Rate limit exceeded",
                    "limit": limit,
                    "window_seconds": window_seconds,
                    "client_ip": client_ip,
                    "route": route,
                },
            )
    except RedisError:
        logger.warning(
            "Redis rate limit check failed for key=%s, allowing request through",
            key,
        )
