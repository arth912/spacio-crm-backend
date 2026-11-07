import os
import ssl
import logging
from fastapi import Request
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

# TLS to the database. Set DB_SSL_CA to the path of Supabase's CA certificate (downloadable from
# the Supabase dashboard -> Database -> SSL) to enable full certificate verification.
# Without it we still encrypt but cannot verify the server (legacy behaviour, logged below).
_ca_file = os.getenv("DB_SSL_CA")
if _ca_file:
    ssl_ctx = ssl.create_default_context(cafile=_ca_file)
else:
    logging.getLogger(__name__).warning(
        "DB_SSL_CA not set: database TLS certificate is NOT verified. Set DB_SSL_CA in production."
    )
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

# Create async engine for Supabase PostgreSQL (connection pooler)
engine = create_async_engine(
    DATABASE_URL,
    echo=os.getenv("SQL_ECHO", "false").lower() == "true",
    pool_size=int(os.getenv("DB_POOL_SIZE", "10")),
    max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
    # A NEW connection to the pooler costs ~1.4s (TCP + TLS + auth) vs ~40ms for a query on a
    # warm one, so the goal is to never open connections on the request path:
    #  - keep them alive with a background ping (see keepalive_loop) so the pooler never drops them
    #  - recycle before the pooler's idle timeout
    #  - pre_ping is a cheap (~40ms) safety net so a dead connection is replaced instead of 500ing
    pool_pre_ping=True,
    pool_recycle=240,
    pool_timeout=15,
    pool_use_lifo=True,  # reuse the hottest connections; spare ones idle out instead of all staying half-stale
    connect_args={
        "ssl": ssl_ctx,
        "statement_cache_size": 0,
        "timeout": 10,
        "command_timeout": 30,
        "server_settings": {"application_name": "spacio-crm"},
    },
)

# Async session factory
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)

Base = declarative_base()

async def get_db(request: Request):
    """
    Dependency generator for FastAPI route endpoints to yield an active database session.
    Read-only verbs skip the commit round-trip.
    """
    read_only = request.method in ("GET", "HEAD", "OPTIONS")
    async with AsyncSessionLocal() as session:
        try:
            if read_only:
                # AUTOCOMMIT skips the BEGIN and ROLLBACK round trips (2 x network latency per request).
                await session.connection(execution_options={"isolation_level": "AUTOCOMMIT"})
            yield session
            if not read_only:
                await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
