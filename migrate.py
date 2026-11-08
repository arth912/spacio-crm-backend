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
        # Billing: template choice lives on the profile; payments can optionally settle a bill
        for ddl in [
            "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS billing_template VARCHAR(20) NOT NULL DEFAULT 'modern'",
            "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS billing_terms TEXT",
            "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS signature TEXT",
            "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS stamp TEXT",
            """CREATE TABLE IF NOT EXISTS bills (
                id VARCHAR(36) PRIMARY KEY,
                user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                client_id VARCHAR(36) NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
                project_id VARCHAR(36) NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                bill_number VARCHAR(30) NOT NULL,
                bill_date DATE NOT NULL,
                due_date DATE,
                status VARCHAR(20) NOT NULL DEFAULT 'draft',
                template VARCHAR(20) NOT NULL DEFAULT 'modern',
                customer_gstin VARCHAR(20),
                notes TEXT,
                subtotal DOUBLE PRECISION NOT NULL DEFAULT 0,
                tax_amount DOUBLE PRECISION NOT NULL DEFAULT 0,
                discount_amount DOUBLE PRECISION NOT NULL DEFAULT 0,
                grand_total DOUBLE PRECISION NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT (now() at time zone 'utc'),
                CONSTRAINT ux_bills_user_number UNIQUE (user_id, bill_number)
            )""",
            """CREATE TABLE IF NOT EXISTS bill_items (
                id VARCHAR(36) PRIMARY KEY,
                bill_id VARCHAR(36) NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
                position INTEGER NOT NULL DEFAULT 0,
                description VARCHAR(200) NOT NULL,
                qty DOUBLE PRECISION NOT NULL,
                rate DOUBLE PRECISION NOT NULL,
                tax_percent DOUBLE PRECISION NOT NULL DEFAULT 0,
                amount DOUBLE PRECISION NOT NULL
            )""",
            "ALTER TABLE bill_items ADD COLUMN IF NOT EXISTS pricing_type VARCHAR(20) NOT NULL DEFAULT 'piece'",
            "ALTER TABLE bill_items ADD COLUMN IF NOT EXISTS length DOUBLE PRECISION NOT NULL DEFAULT 1",
            "ALTER TABLE bill_items ADD COLUMN IF NOT EXISTS breadth DOUBLE PRECISION NOT NULL DEFAULT 1",
            "ALTER TABLE bill_items ADD COLUMN IF NOT EXISTS height DOUBLE PRECISION NOT NULL DEFAULT 1",
            "ALTER TABLE bill_items ADD COLUMN IF NOT EXISTS remark TEXT",
            "ALTER TABLE payments ADD COLUMN IF NOT EXISTS bill_id VARCHAR(36) REFERENCES bills(id) ON DELETE SET NULL",
            "CREATE INDEX IF NOT EXISTS ix_bills_user_created ON bills (user_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS ix_bills_client_id ON bills (client_id)",
            "CREATE INDEX IF NOT EXISTS ix_bills_project_id ON bills (project_id)",
            "CREATE INDEX IF NOT EXISTS ix_bill_items_bill_id ON bill_items (bill_id)",
            "CREATE INDEX IF NOT EXISTS ix_payments_bill_id ON payments (bill_id)",
            # Lock out Supabase's public API (anon/authenticated); the backend role bypasses RLS
            "ALTER TABLE bills ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE bill_items ENABLE ROW LEVEL SECURITY",
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
