from typing import Any


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}

    def body(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


def validation(fields: list[str], message: str = "Request validation failed.") -> ApiError:
    # Preserve order, drop duplicates.
    seen: list[str] = []
    for field in fields:
        if field not in seen:
            seen.append(field)
    return ApiError(400, "VALIDATION_ERROR", message, {"fields": seen})
