import logging
from http import HTTPStatus

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base class for expected application errors."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"
    message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: object | None = None,
    ) -> None:
        super().__init__(message or self.message)
        self.message = message or self.message
        self.details = details


def _payload(exc: AppError) -> dict[str, object]:
    error: dict[str, object] = {"code": exc.code, "message": exc.message}
    if exc.details is not None:
        error["details"] = exc.details
    return {"error": error}


class ResourceNotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"
    message = "The requested resource was not found."


class ProviderUnavailableError(AppError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "provider_unavailable"
    message = "Authentication or database service is temporarily unavailable."


class PermissionDeniedError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "permission_denied"
    message = "You do not have access to this resource."


def _client_error_hints(exc: RequestValidationError) -> list[dict[str, object]]:
    hints: list[dict[str, object]] = []
    for raw in exc.errors():
        if "loc" in raw and "msg" in raw:
            hints.append({"field": raw["loc"], "message": raw["msg"]})
    return hints


def _http_error_code(status_code: int) -> str:
    """Map a status code to a snake_case code, e.g. 404 -> "not_found"."""
    try:
        return HTTPStatus(status_code).phrase.lower().replace(" ", "_")
    except ValueError:
        return "http_error"


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        if exc.status_code >= 500:
            logger.error("Application error: %s (%s)", exc.code, exc.message)
        return JSONResponse(status_code=exc.status_code, content=_payload(exc))

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        _: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        
        error: dict[str, object] = {"code": _http_error_code(exc.status_code)}

        if isinstance(exc.detail, str):
            error["message"] = exc.detail
        else:
            # detail can be a dict/list; keep it under "details"
            error["message"] = HTTPStatus(exc.status_code).phrase
            error["details"] = exc.detail

        if exc.status_code >= 500:
            logger.error("HTTP error %s: %s", exc.status_code, exc.detail)

        # Keep headers such as `WWW-Authenticate` (needed for 401 responses)
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": error},
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "The request payload is invalid.",
                    "details": _client_error_hints(exc),
                }
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
        # Diagnostic context stays in server logs only; never in the response.
        logger.exception("Unhandled exception")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "An unexpected error occurred.",
                }
            },
        )
