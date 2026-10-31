from functools import lru_cache
from redis import Redis
from backend.config import settings
from backend.services.security import DomainError


@lru_cache
def redis_client() -> Redis:
    return Redis.from_url(settings().redis_url, decode_responses=True)


def hit(key: str, seconds: int) -> int:
    redis, name = redis_client(), f"rate:{key}"
    count = redis.incr(name)
    if count == 1 or redis.ttl(name) < 0:  # a fixed window; ttl < 0 repairs a key left without expiry
        redis.expire(name, seconds)
    return count


def rate_limit(key: str, limit: int, seconds: int) -> None:
    if hit(key, seconds) > limit:
        raise DomainError("rate_limit", 429)
