"""FastAPI app factory. Contract: docs/FRONTEND_BACKEND_CONTRACT.md (v0.1.0)."""
import logging
import time
import uuid

from fastapi import FastAPI, Request
from starlette.exceptions import HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from pathlib import Path

from fastapi.staticfiles import StaticFiles

from ai.companion import Companion
from ai.limits import QuotaLimiter
from ai.llm import ProviderChain, providers_from_settings
from api.routes import router
from processing.irsa import CutoutCache, discover
from services.core import BadRequest, DataUnavailable, NotFound, Services, Upstream
from store.registry import Registry

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


def build_default_services() -> Services:
    data = Path(settings.data_dir)
    collections = tuple(c.strip() for c in settings.sia_collections.split(",") if c.strip())
    return Services(Registry(data / "registry.sqlite"), CutoutCache(data / "cutouts"), data / "images",
                    discover=lambda ra, dec: discover(ra, dec, collections=collections))


def build_default_companion(services: Services) -> Companion:
    return Companion(services, ProviderChain(providers_from_settings(settings)),
                     QuotaLimiter(settings.chat_limit_per_client, settings.chat_limit_global_daily, settings.chat_limit_per_minute))


def create_app(services: Services | None = None, companion: Companion | None = None) -> FastAPI:
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    app = FastAPI(title="SPHEREx backend", version=CONTRACT_VERSION)
    app.state.services = services or build_default_services()
    app.state.companion = companion or build_default_companion(app.state.services)
    app.mount("/media/images", StaticFiles(directory=app.state.services.images_dir), name="images")

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

    @app.exception_handler(NotFound)
    async def not_found(request: Request, exc: NotFound):
        return JSONResponse(error_body("NOT_FOUND", str(exc), False, request.state.request_id), status_code=404)

    @app.exception_handler(BadRequest)
    async def bad_request(request: Request, exc: BadRequest):
        return JSONResponse(error_body("INVALID_REQUEST", str(exc), False, request.state.request_id), status_code=400)

    @app.exception_handler(DataUnavailable)
    async def data_unavailable(request: Request, exc: DataUnavailable):
        return JSONResponse(error_body("DATA_UNAVAILABLE", str(exc), False, request.state.request_id), status_code=422)

    @app.exception_handler(Upstream)
    async def upstream(request: Request, exc: Upstream):
        return JSONResponse(error_body("UPSTREAM_UNAVAILABLE", str(exc), exc.retryable, request.state.request_id), status_code=503)

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

    app.include_router(router, prefix="/api/v1")
    if settings.frontend_dir and Path(settings.frontend_dir).is_dir():
        app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")   # must be mounted last
    return app


app = create_app()
