"""FastAPI app factory. Contract: docs/FRONTEND_BACKEND_CONTRACT.md (v0.1.0)."""
import logging
import time
import uuid

from fastapi import FastAPI, Request
from starlette.exceptions import HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import CONTRACT_VERSION, settings

log = logging.getLogger("spx")

# Contract §4 error codes -> default HTTP status.
_CODE_FOR_STATUS = {400: "INVALID_REQUEST", 404: "NOT_FOUND", 429: "RATE_LIMITED", 502: "UPSTREAM_UNAVAILABLE", 503: "UPSTREAM_UNAVAILABLE"}


def error_body(code: str, message: str, retryable: bool = False, request_id: str | None = None, details: dict | None = None) -> dict:
    err: dict = {"code": code, "message": message, "retryable": retryable}
    if details:
        err["details"] = details
    if request_id:
        err["requestId"] = request_id
    return {"error": err}


def create_app() -> FastAPI:
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    app = FastAPI(title="SPHEREx backend", version=CONTRACT_VERSION)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_logging(request: Request, call_next):
        rid = uuid.uuid4().hex[:12]
        request.state.request_id = rid
        start = time.perf_counter()
        response = await call_next(request)
        log.info("%s %s -> %s in %.0f ms [%s]", request.method, request.url.path, response.status_code, (time.perf_counter() - start) * 1000, rid)
        response.headers["X-Request-Id"] = rid
        return response

    @app.exception_handler(HTTPException)
    async def http_exc(request: Request, exc: HTTPException):
        code = _CODE_FOR_STATUS.get(exc.status_code, "INTERNAL")
        return JSONResponse(error_body(code, str(exc.detail), exc.status_code in (429, 502, 503), request.state.request_id), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_exc(request: Request, exc: RequestValidationError):
        return JSONResponse(error_body("INVALID_REQUEST", "The request is not valid.", False, request.state.request_id, {"errors": exc.errors()[:5]}), status_code=400)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error [%s]", getattr(request.state, "request_id", "-"))
        return JSONResponse(error_body("INTERNAL", "Something went wrong on the server.", False, getattr(request.state, "request_id", None)), status_code=500)

    @app.get("/api/v1/health")
    def health():
        return {"status": "ok", "contractVersion": CONTRACT_VERSION}

    return app


app = create_app()
