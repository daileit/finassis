"""Application errors → HTTP error envelope {code, message, details}."""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    status = 400
    code = "bad_request"

    def __init__(self, message: str | None = None, **details: Any) -> None:
        super().__init__(message or self.code)
        self.message = message or self.code
        self.details = details


class NotFound(AppError):
    status = 404
    code = "not_found"


class Forbidden(AppError):
    status = 403
    code = "forbidden"


class Unauthorized(AppError):
    status = 401
    code = "unauthorized"


class Validation(AppError):
    status = 422
    code = "validation"


class Conflict(AppError):
    status = 409
    code = "conflict"


class UnknownTag(AppError):
    status = 409
    code = "unknown_tag"


class AllowanceExhausted(AppError):
    status = 429
    code = "allowance_exhausted"


class Unbalanced(AppError):
    status = 422
    code = "unbalanced_transaction"
