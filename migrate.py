import asyncio
from sqlalchemy import text
from db import engine

async def run_db_migrations():
    async with engine.begin() as conn:
        # NOTE: the email-verification / password-reset columns that used to be added to `users` here
        # now live in `user_auth_tokens` (see migrate_users_split.py). They are intentionally NOT re-added.
        # Performance indexes
        for ddl in [
            "CREATE INDEX IF NOT EXISTS ix_quotations_created_at ON quotations (created_at DESC)",
            "CREATE INDEX IF NOT EXISTS ix_quotations_project_version ON quotations (project_id, version)",
            "CREATE INDEX IF NOT EXISTS ix_payments_payment_date ON payments (payment_date DESC)",
            "CREATE INDEX IF NOT EXISTS ix_clients_created_at ON clients (created_at DESC)",
            "CREATE INDEX IF NOT EXISTS ix_projects_created_at ON projects (created_at DESC)",
            "CREATE INDEX IF NOT EXISTS ix_items_category_name ON items (category_id, name)",
            "CREATE INDEX IF NOT EXISTS ix_users_created_at ON users (created_at DESC)",
            # Composite indexes matching "WHERE owner = ? ORDER BY date DESC" list queries
            "CREATE INDEX IF NOT EXISTS ix_clients_user_created ON clients (user_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS ix_projects_user_created ON projects (user_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS ix_payments_project_date ON payments (project_id, payment_date DESC)",
            "CREATE INDEX IF NOT EXISTS ix_quotations_project_created ON quotations (project_id, created_at DESC)",
        ]:
            await conn.execute(text(ddl))
        # Per-user catalogue: user_id NULL = shared read-only seed rows
        for ddl in [
            "ALTER TABLE categories ADD COLUMN IF NOT EXISTS user_id VARCHAR(36) REFERENCES users(id) ON DELETE CASCADE",
            "ALTER TABLE items ADD COLUMN IF NOT EXISTS user_id VARCHAR(36) REFERENCES users(id) ON DELETE CASCADE",
            "CREATE INDEX IF NOT EXISTS ix_categories_user_id ON categories (user_id)",
            "CREATE INDEX IF NOT EXISTS ix_items_user_id ON items (user_id)",
            # name uniqueness is now per owner instead of global
            "ALTER TABLE categories DROP CONSTRAINT IF EXISTS categories_name_key",
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_categories_owner_name ON categories (COALESCE(user_id, ''), lower(name))",
        ]:
            await conn.execute(text(ddl))
        print("Database migrations completed successfully.")

async def add_unique_payment_index():
    # Separate transaction: fails harmlessly if legacy duplicate payment ids exist
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_sub_payments_payment_id ON subscription_payments (razorpay_payment_id);"))
    except Exception as e:
        print(f"Skipped unique payment index (duplicates exist?): {e}")

if __name__ == "__main__":
    async def _main():
        await run_db_migrations()
        await add_unique_payment_index()
    asyncio.run(_main())
