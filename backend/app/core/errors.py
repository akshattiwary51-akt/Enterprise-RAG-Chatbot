"""Consistent JSON error responses. Never leaks stack traces or DB errors."""
from __future__ import annotations

import logging

from flask import Flask, g, jsonify
from marshmallow import ValidationError
from werkzeug.exceptions import HTTPException

log = logging.getLogger("app.errors")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details


def error_response(status: int, code: str, message: str, details: dict | None = None):
    body = {
        "error": {
            "code": code,
            "message": message,
            "correlation_id": getattr(g, "correlation_id", None),
        }
    }
    if details:
        body["error"]["details"] = details
    return jsonify(body), status


# Convenience constructors -------------------------------------------------
def bad_request(message="Invalid request", code="bad_request", details=None):
    return ApiError(400, code, message, details)


def unauthorized(message="Authentication required", code="unauthorized"):
    return ApiError(401, code, message)


def forbidden(message="You do not have permission to perform this action", code="forbidden"):
    return ApiError(403, code, message)


def not_found(message="Resource not found", code="not_found"):
    return ApiError(404, code, message)


def conflict(message, code="conflict"):
    return ApiError(409, code, message)


def unavailable(message, code="service_unavailable"):
    return ApiError(503, code, message)


_STATUS_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "unprocessable",
    429: "rate_limited",
}


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(ApiError)
    def _api_error(err: ApiError):
        return error_response(err.status, err.code, err.message, err.details)

    @app.errorhandler(ValidationError)
    def _validation(err: ValidationError):
        return error_response(400, "validation_error", "Request validation failed", {"fields": err.messages})

    @app.errorhandler(HTTPException)
    def _http(err: HTTPException):
        status = err.code or 500
        code = _STATUS_CODES.get(status, "http_error")
        message = {
            404: "Resource not found",
            405: "Method not allowed",
            413: "Request body is too large",
            429: "Too many requests; please slow down",
        }.get(status, err.description if status < 500 else "Internal server error")
        return error_response(status, code, message)

    @app.errorhandler(Exception)
    def _unhandled(err: Exception):
        # Log type + correlation id only. Exception text can contain SQL or
        # document fragments, so the traceback goes to server logs, never to the client.
        log.exception("Unhandled error (correlation_id=%s)", getattr(g, "correlation_id", None))
        return error_response(500, "internal_error", "An unexpected error occurred")
