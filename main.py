import os
import sys
import logging
import logger_setup

logger = logging.getLogger(__name__)

# Patch cffi dynamic library loader to search Homebrew paths on macOS before weasyprint is imported
if sys.platform == 'darwin':
    try:
        import cffi
        orig_dlopen = cffi.FFI.dlopen
        def custom_dlopen(self, name, flags=0):
            if name and not name.startswith('/') and not os.path.exists(name):
                for prefix in ['/opt/homebrew/lib', '/usr/local/lib']:
                    clean_name = name
                    if name.startswith('lib'):
                        clean_name = name[3:]
                    for suffix in ['-2.0-0', '-1.0-0', '-2.0', '-1.0', '-0']:
                        if clean_name.endswith(suffix):
                            clean_name = clean_name[:-len(suffix)]
                    
                    possible_names = [
                        os.path.join(prefix, name),
                        os.path.join(prefix, f'lib{clean_name}.dylib'),
                        os.path.join(prefix, f'lib{clean_name}-2.0.dylib'),
                        os.path.join(prefix, f'lib{clean_name}-1.0.dylib'),
                        os.path.join(prefix, f'lib{clean_name}.0.dylib'),
                        os.path.join(prefix, f'lib{clean_name}.1.dylib'),
                    ]
                    for p_name in possible_names:
                        if os.path.exists(p_name):
                            try:
                                return orig_dlopen(self, p_name, flags)
                            except OSError:
                                pass
            return orig_dlopen(self, name, flags)
        cffi.FFI.dlopen = custom_dlopen
    except Exception as e:
        logger.error(f"Failed to patch cffi dlopen: {e}")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from db import engine
from expiry import expiry_loop
from routers import auth, users, clients, projects, catalog, quotations, payments, billing, feedback

app = FastAPI(
    title="decoCRM API Engine",
    description="Backend API for decoCRM - Modular, Secure, High Performance Interior Design CRM Engine",
    version="2.0.0"
)

import asyncio

WARM_CONNECTIONS = int(os.getenv("DB_WARM_CONNECTIONS", "5"))
KEEPALIVE_SECONDS = int(os.getenv("DB_KEEPALIVE_SECONDS", "30"))

async def _ping_pool(n: int) -> None:
    """Open/touch n pooled connections concurrently (each new one costs ~1.4s to the pooler)."""
    async def one():
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            await asyncio.sleep(0.05)  # hold briefly so the n pings use n distinct connections
    await asyncio.gather(*(one() for _ in range(n)), return_exceptions=True)

async def _keepalive_loop() -> None:
    # The Supabase pooler drops idle connections; without this the first request after a quiet
    # period pays a ~1.4s reconnect (or hits a dead connection).
    while True:
        await asyncio.sleep(KEEPALIVE_SECONDS)
        try:
            await _ping_pool(WARM_CONNECTIONS)
        except Exception as e:
            logger.warning(f"DB keepalive failed: {e}")

@app.on_event("startup")
async def startup_event():
    print("=" * 50)
    print("🚀 Server started successfully on http://127.0.0.1:8000")
    try:
        await _ping_pool(WARM_CONNECTIONS)  # pre-warm so the first page load doesn't pay connect cost
        print(f"🔗 Database connected successfully! ({WARM_CONNECTIONS} connections warmed)")
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        logger.error(f"Database connection failed during startup: {e}")
    app.state.keepalive_task = asyncio.create_task(_keepalive_loop())
    app.state.expiry_task = asyncio.create_task(expiry_loop())
    print("=" * 50)

@app.on_event("shutdown")
async def shutdown_event():
    for name in ("keepalive_task", "expiry_task"):
        task = getattr(app.state, name, None)
        if task:
            task.cancel()
    await engine.dispose()

# --- Security & performance middleware ---
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",") if o.strip()]
ALLOWED_HOSTS = [h.strip() for h in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",") if h.strip()]
MAX_BODY_BYTES = int(os.getenv("MAX_BODY_BYTES", str(5 * 1024 * 1024)))  # 5 MB (logos are base64)

class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
            return JSONResponse({"detail": "Request body too large"}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if request.url.path.startswith("/api/") and "cache-control" not in response.headers:
            response.headers["Cache-Control"] = "no-store"
        return response

app.add_middleware(SecurityMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# Include Routers
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(clients.router)
app.include_router(projects.router)
app.include_router(catalog.router)
app.include_router(quotations.router)
app.include_router(payments.router)
app.include_router(billing.router)
app.include_router(feedback.router)

@app.get("/")
async def root():
    return {
        "status": "online",
        "app": "decoCRM API Engine",
        "version": "2.0.0"
    }
