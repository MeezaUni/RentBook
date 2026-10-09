from __future__ import annotations


class DomainError(Exception):
    """A business-rule violation that should be shown to the user (HTTP 4xx)."""

    status_code = 400

    def __init__(self, code: str, message: str, details: dict | None = None, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        if status is not None:
            self.status_code = status


class NotFound(DomainError):
    status_code = 404

    def __init__(self, what: str):
        super().__init__("not_found", f"{what} not found")


class Conflict(DomainError):
    status_code = 409
