"""Automatic deactivation of users whose subscription has expired."""
import asyncio
import logging
from sqlalchemy import text

from db import AsyncSessionLocal
from deps import clear_cached_token_version

logger = logging.getLogger(__name__)

EXPIRY_CHECK_SECONDS = 3600

# Idempotent, so it is safe if several workers run it. Admin and Sales are not subscription-based.
_DEACTIVATE_SQL = text("""
    UPDATE user_subscriptions s SET status = 'deactivated'
    FROM users u
    WHERE u.id = s.user_id
      AND u.role NOT IN ('Admin', 'Sales')
      AND s.status = 'active'
      AND s.access_end IS NOT NULL
      AND s.access_end < (now() at time zone 'utc')
    RETURNING s.user_id
""")


async def deactivate_expired_users() -> int:
    async with AsyncSessionLocal() as db:
        ids = [r[0] for r in (await db.execute(_DEACTIVATE_SQL)).all()]
        if ids:
            # Sign them out everywhere; they can log in again only through the renewal payment flow.
            await db.execute(
                text("UPDATE users SET token_version = COALESCE(token_version, 0) + 1 WHERE id = ANY(:ids)"),
                {"ids": ids},
            )
        await db.commit()
    for uid in ids:
        clear_cached_token_version(uid)
    if ids:
        logger.info(f"Deactivated {len(ids)} user(s) with expired subscriptions")
    return len(ids)


async def expiry_loop() -> None:
    while True:
        try:
            await deactivate_expired_users()
        except Exception as e:
            logger.warning(f"Subscription expiry check failed: {e}")
        await asyncio.sleep(EXPIRY_CHECK_SECONDS)
