import time

import redis

from app.core.config import settings

redis_client = redis.from_url(settings.REDIS_URL)


class RateLimiter:
    """
    Per-account fixed-window rate limiter backed by Redis. Only ever
    consulted for bulk actions that supply an account_id - accounts
    without one aren't rate-limited at all.
    """

    def __init__(self, limit_per_minute: int | None = None):
        self.limit_per_minute = limit_per_minute or settings.RATE_LIMIT_PER_MINUTE

    def try_consume(self, account_id: int, count: int) -> bool:
        """
        Reserves `count` units of this minute's budget for `account_id`.
        Returns True if the reservation fit within the limit; if it
        didn't, the reservation is rolled back (so a denied request
        doesn't still eat into the budget) and False is returned.
        """
        bucket = f"rate:{account_id}:{int(time.time() // 60)}"

        pipe = redis_client.pipeline()
        pipe.incrby(bucket, count)
        pipe.expire(bucket, 90)  # a bit more than 60s so it outlives the window it's counting
        new_total, _ = pipe.execute()

        if new_total > self.limit_per_minute:
            redis_client.decrby(bucket, count)
            return False

        return True
