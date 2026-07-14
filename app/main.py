from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.api import router
from app.config import settings
from app.database import engine
from app.errors import APIError


logger = logging.getLogger("approval_service")


def create_app(database_engine: Engine | None = None) -> FastAPI:
    app = FastAPI(
        title="Approval Service",
        version=settings.app_version,
        description="Workspace-scoped content approval workflow service.",
    )
    app.state.db_engine = database_engine or engine

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(APIError)
    async def api_error_handler(_request: Request, exc: APIError) -> JSONResponse:
        error: dict[str, Any] = {"code": exc.code, "message": exc.message}
        if exc.details:
            error["details"] = exc.details
        return JSONResponse(status_code=exc.status_code, content={"error": error})

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Pydantic's default errors include the submitted input. Returning only an
        # allowlist prevents an accidental secret/token echo in a public response.
        details = [
            {
                "type": item.get("type", "validation_error"),
                "location": [str(part) for part in item.get("loc", ())],
                "message": item.get("msg", "Invalid request"),
            }
            for item in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "Request validation failed",
                    "details": details,
                }
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        # Never log request bodies, headers, SQL parameters or exception messages.
        logger.error(
            "unhandled_error request_id=%s error_type=%s",
            getattr(request.state, "request_id", "unknown"),
            type(exc).__name__,
        )
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "internal_error", "message": "Internal server error"}},
        )

    @app.get("/health", tags=["service"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready", tags=["service"])
    def ready(request: Request) -> JSONResponse:
        try:
            with request.app.state.db_engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse(status_code=503, content={"status": "not_ready"})
        return JSONResponse(status_code=200, content={"status": "ready"})

    app.include_router(router, tags=["approval-requests"])
    return app


app = create_app()

