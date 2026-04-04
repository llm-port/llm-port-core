from __future__ import annotations

from fastapi.responses import JSONResponse


class GatewayError(Exception):
    """Typed error raised by gateway services."""

    def __init__(
        self,
        *,
        status_code: int,
        message: str,
        error_type: str = "invalid_request_error",
        param: str | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.error_type = error_type
        self.param = param
        self.code = code


def error_response(
    *,
    status_code: int,
    message: str,
    error_type: str = "invalid_request_error",
    param: str | None = None,
    code: str | None = None,
) -> JSONResponse:
    """Build an OpenAI-compatible error envelope."""
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "type": error_type,
                "message": message,
                "param": param,
                "code": code,
            },
        },
    )
