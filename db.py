import os
import ssl
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base

# Load environment variables from .env file
load_dotenv()

# Try supabase_pooler_url first (since direct URL might use IPv6 which fails on some networks), then fallback to supabase_url
DATABASE_URL = os.getenv("supabase_pooler_url")
if not DATABASE_URL:
    DATABASE_URL = os.getenv("supabase_url")
if not DATABASE_URL:
    DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("Neither supabase_pooler_url nor supabase_url environment variable is set. Please check your .env file.")

# Translate protocol prefix for asyncpg driver compatibility
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

# Create SSL context for Supabase (macOS Python 3.9 doesn't include Supabase's intermediate CA)
ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

# Create async engine for Supabase PostgreSQL (connection pooler)
engine = create_async_engine(
    DATABASE_URL,
    echo=True,
    connect_args={
        "ssl": ssl_ctx,
        "statement_cache_size": 0
    },
)

# Async session factory
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)

Base = declarative_base()

async def get_db():
    """
    Dependency generator for FastAPI route endpoints to yield an active database session.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
