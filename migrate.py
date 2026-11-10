import asyncio
from sqlalchemy import text
from db import engine

async def promote_user_catalogues_to_general(conn):
    """One-time: merge every user's private catalogue into the general (user_id NULL) catalogue.

    Categories are merged by name and items by (category, name). The first row found becomes the
    general row; each owner's rate is kept as a private override in user_item_rates, and
    quotation_items are re-pointed at the surviving rows. Guarded by a flag so it runs once.
    """
    await conn.execute(text("CREATE TABLE IF NOT EXISTS schema_flags (name VARCHAR(100) PRIMARY KEY)"))
    done = (await conn.execute(text("SELECT 1 FROM schema_flags WHERE name = 'general_catalogue_v1'"))).first()
    if done:
        return
    import uuid
    cats = (await conn.execute(text("SELECT id, name FROM categories WHERE user_id IS NOT NULL ORDER BY created_at, id"))).all()
    cat_canon, cat_map = {}, {}
    for cid, name in cats:
        key = (name or "").strip().lower()
        cat_canon.setdefault(key, cid)
        cat_map[cid] = cat_canon[key]
    items = (await conn.execute(text(
        "SELECT id, category_id, name, base_rate, labor_cost, user_id FROM items WHERE user_id IS NOT NULL ORDER BY created_at, id"))).all()
    item_canon, item_map, rates = {}, {}, []
    for iid, cat_id, name, rate, labor, owner in items:
        ccat = cat_map.get(cat_id, cat_id)
        key = (ccat, (name or "").strip().lower())
        item_canon.setdefault(key, iid)
        item_map[iid] = (item_canon[key], ccat)
        rates.append((owner, item_canon[key], rate, labor))
    # private rates first (needs owners), then re-point references, then drop duplicates
    seen = set()
    for owner, item_id, rate, labor in rates:
        if (owner, item_id) in seen:
            continue
        seen.add((owner, item_id))
        await conn.execute(text(
            "INSERT INTO user_item_rates (id, user_id, item_id, base_rate, labor_cost) "
            "SELECT CAST(:id AS VARCHAR), CAST(:u AS VARCHAR), CAST(:i AS VARCHAR), CAST(:r AS DOUBLE PRECISION), CAST(:l AS DOUBLE PRECISION) "
            "WHERE NOT EXISTS (SELECT 1 FROM user_item_rates WHERE user_id = CAST(:u AS VARCHAR) AND item_id = CAST(:i AS VARCHAR))"),
            {"id": str(uuid.uuid4()), "u": owner, "i": item_id, "r": rate, "l": labor or 0.0})
    for iid, (canon_id, ccat) in item_map.items():
        if canon_id != iid:
            await conn.execute(text("UPDATE quotation_items SET item_id = :c, category_id = :cc WHERE item_id = :i"),
                               {"c": canon_id, "cc": ccat, "i": iid})
    for cid, canon_id in cat_map.items():
        if canon_id != cid:
            await conn.execute(text("UPDATE quotation_items SET category_id = :c WHERE category_id = :i"), {"c": canon_id, "i": cid})
    for iid, (canon_id, ccat) in item_map.items():
        if canon_id != iid:
            await conn.execute(text("DELETE FROM items WHERE id = :i"), {"i": iid})
        else:
            await conn.execute(text("UPDATE items SET category_id = :cc, user_id = NULL WHERE id = :i"), {"cc": ccat, "i": iid})
    for cid, canon_id in cat_map.items():
        if canon_id != cid:
            await conn.execute(text("DELETE FROM categories WHERE id = :i"), {"i": cid})
        else:
            await conn.execute(text("UPDATE categories SET user_id = NULL WHERE id = :i"), {"i": cid})
    await conn.execute(text("INSERT INTO schema_flags (name) VALUES ('general_catalogue_v1')"))
    print(f"General catalogue: promoted {len(set(cat_map.values()))} categories, {len(set(v[0] for v in item_map.values()))} items.")

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
            # general catalogue (user_id NULL) managed by Admin + per-user private rate overrides
            "ALTER TABLE categories ALTER COLUMN user_id DROP NOT NULL",
            "ALTER TABLE items ALTER COLUMN user_id DROP NOT NULL",
            """CREATE TABLE IF NOT EXISTS user_item_rates (
                id VARCHAR(36) PRIMARY KEY,
                user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                item_id VARCHAR(36) NOT NULL REFERENCES items(id) ON DELETE CASCADE,
                base_rate DOUBLE PRECISION NOT NULL,
                labor_cost DOUBLE PRECISION DEFAULT 0,
                updated_at TIMESTAMP DEFAULT now()
            )""",
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_user_item_rates ON user_item_rates (user_id, item_id)",
            "ALTER TABLE user_item_rates ENABLE ROW LEVEL SECURITY",
        ]:
            await conn.execute(text(ddl))
        # Billing: template choice lives on the profile; payments can optionally settle a bill
        for ddl in [
            "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS billing_template VARCHAR(20) NOT NULL DEFAULT 'modern'",
            "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS billing_terms TEXT",
            "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS signature TEXT",
            "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS stamp TEXT",
            "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS tour_completed BOOLEAN NOT NULL DEFAULT FALSE",
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
        # Subscription status: expired non Admin/Sales users are deactivated automatically (expiry.py)
        for ddl in [
            "ALTER TABLE user_subscriptions ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'active'",
            """UPDATE user_subscriptions s SET status = 'deactivated'
               FROM users u
               WHERE u.id = s.user_id AND u.role NOT IN ('Admin', 'Sales')
                 AND s.access_end IS NOT NULL AND s.access_end < (now() at time zone 'utc')
                 AND s.status = 'active'""",
        ]:
            await conn.execute(text(ddl))
        await promote_user_catalogues_to_general(conn)
        await conn.execute(text("ALTER TABLE schema_flags ENABLE ROW LEVEL SECURITY"))
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
