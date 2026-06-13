import asyncio
from sqlalchemy import text
from db import engine

async def run_db_migrations():
    async with engine.begin() as conn:
        print("Running database migrations for email verification and password reset...")
        
        # 1. Add is_email_verified column without default/not-null initially
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_email_verified BOOLEAN;"))
        
        # 2. Update existing users to be verified so they aren't locked out
        await conn.execute(text("UPDATE users SET is_email_verified = TRUE WHERE is_email_verified IS NULL;"))
        
        # 3. Apply default and NOT NULL constraints to is_email_verified
        await conn.execute(text("ALTER TABLE users ALTER COLUMN is_email_verified SET DEFAULT FALSE;"))
        await conn.execute(text("ALTER TABLE users ALTER COLUMN is_email_verified SET NOT NULL;"))
        
        # 4. Add the rest of the columns
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verification_token VARCHAR(50);"))
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verification_expires_at TIMESTAMP;"))
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_token VARCHAR(50);"))
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_token_expires_at TIMESTAMP;"))
        print("Database migrations completed successfully.")

if __name__ == "__main__":
    asyncio.run(run_db_migrations())
