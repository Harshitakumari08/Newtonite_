from __future__ import annotations
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.routes import router
from app.config import settings
from app.db import engine
from app.errors import install_error_handlers

app = FastAPI(title="Operations Under Pressure", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Idempotent-Replayed"],
)
install_error_handlers(app)
app.include_router(router, prefix="/api")

# Direct root-level aliases for auth endpoints
from app.api.routes import login as login_handler, me as me_handler
from app.schemas import TokenOut, MeOut

app.add_api_route("/auth/login", login_handler, methods=["POST"], response_model=TokenOut, tags=["auth"], include_in_schema=False)
app.add_api_route("/login", login_handler, methods=["POST"], response_model=TokenOut, tags=["auth"], include_in_schema=False)
app.add_api_route("/me", me_handler, methods=["GET"], response_model=MeOut, tags=["auth"], include_in_schema=False)


@app.get("/health", tags=["Health"])
@app.get("/api/health", tags=["Health"], include_in_schema=False)
def health():
    db_status = "connected"
    system_status = "healthy"
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        db_status = "disconnected"
        system_status = "degraded"

    return {
        "status": system_status,
        "version": app.version,
        "database": db_status,
        "service": "newtonite-api",
    }


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True}
