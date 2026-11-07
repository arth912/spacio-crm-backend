"""
Split the wide `users` table into users + user_profiles + user_subscriptions + user_auth_tokens.

Two phases, run manually (take a DB backup first - see backups/backup_db.py):

  python migrate_users_split.py phase1   # create tables, backfill, relax old NOT NULL columns. Safe/re-runnable.
  ... deploy the new application code, verify login/register/payment/reset/PDF ...
  python migrate_users_split.py phase2   # DROP the old columns from `users`. Irreversible.

Until phase2 runs, the old columns stay in place (rollback = redeploy old code; any users
registered / profile edits made after phase1 will only exist in the new tables).
"""
import asyncio
import sys
from sqlalchemy import text
from db import engine

PHASE1 = [
    """CREATE TABLE IF NOT EXISTS user_profiles (
        user_id VARCHAR(36) PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        company_logo TEXT,
        general_terms TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS user_subscriptions (
        user_id VARCHAR(36) PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        payment_status VARCHAR(50) NOT NULL DEFAULT 'unpaid',
        selected_plan VARCHAR(50) NOT NULL DEFAULT 'monthly',
        access_start TIMESTAMP,
        access_end TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS user_auth_tokens (
        user_id VARCHAR(36) PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        verification_token VARCHAR(128),
        verification_expires_at TIMESTAMP,
        verification_attempts INTEGER NOT NULL DEFAULT 0,
        reset_token VARCHAR(128),
        reset_expires_at TIMESTAMP,
        reset_attempts INTEGER NOT NULL DEFAULT 0
    )""",
    # Backfill (idempotent)
    """INSERT INTO user_profiles (user_id, company_logo, general_terms)
       SELECT id, company_logo, general_terms FROM users
       ON CONFLICT (user_id) DO NOTHING""",
    """INSERT INTO user_subscriptions (user_id, payment_status, selected_plan, access_start, access_end)
       SELECT id, COALESCE(payment_status, 'unpaid'), COALESCE(selected_plan, 'monthly'), access_start, access_end
       FROM users
       ON CONFLICT (user_id) DO NOTHING""",
    # The old shared otp_attempts counter is copied to whichever flow has an active token.
    """INSERT INTO user_auth_tokens (user_id, verification_token, verification_expires_at, verification_attempts,
                                    reset_token, reset_expires_at, reset_attempts)
       SELECT id, email_verification_token, email_verification_expires_at,
              CASE WHEN email_verification_token IS NOT NULL THEN COALESCE(otp_attempts, 0) ELSE 0 END,
              reset_token, reset_token_expires_at,
              CASE WHEN reset_token IS NOT NULL THEN COALESCE(otp_attempts, 0) ELSE 0 END
       FROM users
       WHERE email_verification_token IS NOT NULL OR reset_token IS NOT NULL
       ON CONFLICT (user_id) DO NOTHING""",
    # New code no longer writes these old columns: relax constraints so inserts into `users` still work.
    "ALTER TABLE users ALTER COLUMN payment_status DROP NOT NULL",
    "ALTER TABLE users ALTER COLUMN selected_plan DROP NOT NULL",
    "ALTER TABLE users ALTER COLUMN otp_attempts DROP NOT NULL",
]

PHASE2 = [
    "ALTER TABLE users DROP COLUMN IF EXISTS company_logo",
    "ALTER TABLE users DROP COLUMN IF EXISTS general_terms",
    "ALTER TABLE users DROP COLUMN IF EXISTS payment_status",
    "ALTER TABLE users DROP COLUMN IF EXISTS selected_plan",
    "ALTER TABLE users DROP COLUMN IF EXISTS access_start",
    "ALTER TABLE users DROP COLUMN IF EXISTS access_end",
    "ALTER TABLE users DROP COLUMN IF EXISTS email_verification_token",
    "ALTER TABLE users DROP COLUMN IF EXISTS email_verification_expires_at",
    "ALTER TABLE users DROP COLUMN IF EXISTS reset_token",
    "ALTER TABLE users DROP COLUMN IF EXISTS reset_token_expires_at",
    "ALTER TABLE users DROP COLUMN IF EXISTS otp_attempts",
]

async def run(statements):
    async with engine.begin() as conn:  # single transaction: all or nothing
        for ddl in statements:
            await conn.execute(text(ddl))
    print(f"Applied {len(statements)} statements.")

if __name__ == "__main__":
    phase = sys.argv[1] if len(sys.argv) > 1 else ""
    if phase == "phase1":
        asyncio.run(run(PHASE1))
    elif phase == "phase2":
        asyncio.run(run(PHASE2))
    else:
        print(__doc__)
        sys.exit(1)
