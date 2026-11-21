"""Small in-process sliding-window rate limiter.

Good enough for a single backend replica. For horizontal scaling, swap the
storage for Redis (same interface).
"""
from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import HTTPException, status


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def hit(self, key: str, limit: int, window_seconds: float) -> None:
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > window_seconds:
            q.popleft()
        if len(q) >= limit:
            retry = int(window_seconds - (now - q[0])) + 1
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests, slow down.",
                headers={"Retry-After": str(retry)},
            )
        q.append(now)

    def reset(self) -> None:
        self._hits.clear()


limiter = RateLimiter()
