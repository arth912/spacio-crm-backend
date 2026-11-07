"""Small in-memory sliding-window rate limiter (per process).

Usage:  @router.post("/login", dependencies=[Depends(rate_limit("login", 10, 60))])
For multi-worker / multi-instance deployments swap the store for Redis.
"""
import os
import time
from collections import defaultdict, deque
from fastapi import HTTPException, Request

_TRUST_PROXY = os.getenv("TRUST_PROXY", "false").lower() == "true"
_hits: dict = defaultdict(deque)
_last_sweep = 0.0


def _client_ip(request: Request) -> str:
    if _TRUST_PROXY:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(name: str, limit: int, window_seconds: int):
    async def _dep(request: Request):
        global _last_sweep
        now = time.monotonic()
        key = (name, _client_ip(request))
        q = _hits[key]
        while q and now - q[0] > window_seconds:
            q.popleft()
        if len(q) >= limit:
            retry = max(1, int(window_seconds - (now - q[0])))
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please try again later.",
                headers={"Retry-After": str(retry)},
            )
        q.append(now)
        # occasional cleanup so the dict cannot grow without bound
        if now - _last_sweep > 300:
            _last_sweep = now
            for k in [k for k, v in _hits.items() if not v or now - v[-1] > 3600]:
                _hits.pop(k, None)
    return _dep
