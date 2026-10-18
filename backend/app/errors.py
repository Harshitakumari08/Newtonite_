from __future__ import annotations
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse


class AppError(Exception):
    """An expected, client-facing error. Rendered as {"error": {code, message, details}}."""

    def __init__(self, status: int, code: str, message: str, details: dict[str, Any] | None = None):
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}


def not_found(what: str = "Item") -> AppError:
    return AppError(404, "not_found", f"{what} not found")


def forbidden(message: str = "You do not have permission to do this") -> AppError:
    return AppError(403, "forbidden", message)


def error_body(code: str, message: str, details: dict[str, Any] | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": jsonable_encoder(details or {})}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        return JSONResponse(status_code=exc.status, content=error_body(exc.code, exc.message, exc.details))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content=error_body("validation_error", "Request is invalid", {"errors": exc.errors()}),
        )
