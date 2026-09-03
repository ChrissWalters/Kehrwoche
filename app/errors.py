"""Unified error format: ``{"error": {"code", "message", "field?", "message_key?", "params?"}}``.

Every failure leaving the API — raised by us, by FastAPI or unexpectedly — is translated
into this shape by the handlers registered in :func:`register_exception_handlers`.

``message`` is English and stays that way: it is what ends up in logs and in the hands of
anyone driving the API directly. What a person reads on their phone comes from
``message_key``, an i18n key the client resolves in their own language — the same reasoning
that makes notifications store keys instead of finished sentences. A key is only worth
adding where it says more than the code does; ``username_taken`` needs no second wording,
while the five different reasons behind ``not_found`` very much do. Where none is given,
the client falls back to a general text for the code.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class ErrorCode(StrEnum):
    """Machine readable error codes. Clients branch on these, never on messages."""

    VALIDATION_ERROR = "validation_error"
    WEAK_PASSWORD = "weak_password"
    NOT_AUTHENTICATED = "not_authenticated"
    INVALID_CREDENTIALS = "invalid_credentials"
    CSRF_FAILED = "csrf_failed"
    PASSWORD_CHANGE_REQUIRED = "password_change_required"
    USERNAME_TAKEN = "username_taken"
    EMAIL_TAKEN = "email_taken"
    ALREADY_IN_HOUSEHOLD = "already_in_household"
    LAST_ADMIN = "last_admin"
    UNDO_WINDOW_EXPIRED = "undo_window_expired"
    SHARES_MISMATCH = "shares_mismatch"
    CANNOT_TARGET_SELF = "cannot_target_self"
    ACCOUNT_INACTIVE = "account_inactive"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    CONFLICT = "conflict"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    RATE_LIMITED = "rate_limited"
    INTERNAL_ERROR = "internal_error"
    SERVICE_UNAVAILABLE = "service_unavailable"


#: Fallback mapping for HTTP errors raised without an explicit application code.
_CODE_BY_STATUS: dict[int, ErrorCode] = {
    400: ErrorCode.VALIDATION_ERROR,
    401: ErrorCode.NOT_AUTHENTICATED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.METHOD_NOT_ALLOWED,
    409: ErrorCode.CONFLICT,
    413: ErrorCode.PAYLOAD_TOO_LARGE,
    429: ErrorCode.RATE_LIMITED,
    503: ErrorCode.SERVICE_UNAVAILABLE,
}


class AppError(Exception):
    """Business error raised by services and routers."""

    def __init__(
        self,
        status_code: int,
        code: ErrorCode | str,
        message: str,
        field: str | None = None,
        headers: dict[str, str] | None = None,
        *,
        message_key: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = str(code)
        self.message = message
        self.field = field
        self.headers = headers
        self.message_key = message_key
        self.params = params


def error_response(
    status_code: int,
    code: ErrorCode | str,
    message: str,
    field: str | None = None,
    headers: dict[str, str] | None = None,
    *,
    message_key: str | None = None,
    params: dict[str, Any] | None = None,
) -> JSONResponse:
    error: dict[str, Any] = {"code": str(code), "message": message}
    if field is not None:
        error["field"] = field
    if message_key is not None:
        error["message_key"] = message_key
    if params:
        error["params"] = params
    return JSONResponse(status_code=status_code, content={"error": error}, headers=headers)


def _field_path(location: tuple[Any, ...]) -> str | None:
    """Turn a pydantic error location into a client-facing field path.

    ``("body", "shares", 0, "user_id")`` becomes ``"shares.0.user_id"``; the leading
    request part (``body``, ``query``, …) is dropped because it carries no information
    for the form that has to highlight the field.
    """
    parts = [str(part) for part in location]
    if len(parts) > 1 and parts[0] in {"body", "query", "path", "header", "cookie"}:
        parts = parts[1:]
    return ".".join(parts) if parts else None


def http_exception_response(exc: StarletteHTTPException) -> JSONResponse:
    """An HTTP error from FastAPI or Starlette in our error format."""
    code = _CODE_BY_STATUS.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
    detail = exc.detail
    message = detail if isinstance(detail, str) else str(jsonable_encoder(detail))
    return error_response(exc.status_code, code, message, headers=exc.headers)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return error_response(
            exc.status_code,
            exc.code,
            exc.message,
            exc.field,
            exc.headers,
            message_key=exc.message_key,
            params=exc.params,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        return error_response(
            400,
            ErrorCode.VALIDATION_ERROR,
            str(first.get("msg", "Invalid request.")),
            _field_path(tuple(first.get("loc", ()))),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return http_exception_response(exc)

    @app.exception_handler(Exception)
    async def _unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return error_response(500, ErrorCode.INTERNAL_ERROR, "An unexpected error occurred.")
