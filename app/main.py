"""FastAPI application factory and system endpoints."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.config import DEFAULT_CURRENCY, Settings, TlsMode, configure_logging, get_settings
from app.db import get_db, get_session_factory
from app.deps import AdminUser, CurrentUser, SettingsDep
from app.errors import (
    AppError,
    ErrorCode,
    http_exception_response,
    register_exception_handlers,
)
from app.i18n import available_locales, load_catalogue
from app.images import resolve_image
from app.middleware import CsrfMiddleware, SecurityHeadersMiddleware
from app.routers import auth as auth_router
from app.routers import chores as chores_router
from app.routers import expenses as expenses_router
from app.routers import feed as feed_router
from app.routers import household as household_router
from app.routers import notifications as notifications_router
from app.routers import shopping as shopping_router
from app.routers import users as users_router
from app.security import RateLimiters, ensure_certificate
from app.tasks import run_scheduler, watch_certificate

logger = logging.getLogger(__name__)

API_PREFIX = "/api/v1"
STATIC_DIR = Path(__file__).parent / "static"

system_router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    status: Literal["ok"]


class MetaResponse(BaseModel):
    version: str
    languages: list[str]
    default_currency: str
    #: True when this instance serves plain HTTP. The interface says so; the log does too.
    insecure_transport: bool
    #: False when this instance is closed: the sign-up form is hidden instead of failing.
    registration_open: bool


@system_router.get("/health", response_model=HealthResponse, summary="Health probe")
def health(db: Annotated[Session, Depends(get_db)]) -> HealthResponse:
    """Unauthenticated health check: process plus database connection."""
    try:
        db.connection()
    except SQLAlchemyError as exc:
        raise AppError(503, ErrorCode.SERVICE_UNAVAILABLE, "Database connection failed.") from exc
    return HealthResponse(status="ok")


@system_router.get("/meta", response_model=MetaResponse, summary="Instance metadata")
def meta(settings: SettingsDep) -> MetaResponse:
    """Static information a client needs before signing in."""
    return MetaResponse(
        version=__version__,
        languages=available_locales(settings),
        default_currency=DEFAULT_CURRENCY,
        insecure_transport=not settings.tls_enabled,
        registration_open=settings.registration_open,
    )


@system_router.get(
    "/locales/{code}",
    response_model=dict[str, Any],
    summary="Language catalogue",
)
def locale(code: str, settings: SettingsDep) -> dict[str, Any]:
    """Unauthenticated: the sign-in view needs its texts before anybody is signed in."""
    catalogue = load_catalogue(code, settings)
    if catalogue is None:
        raise AppError(
            404,
            ErrorCode.NOT_FOUND,
            "Unknown language.",
            "code",
            message_key="error.locale.unknown",
        )
    return catalogue


#: Pictures are named after their own content, so a name never points at new bytes.
#: A year of caching is safe and saves the phone a request per avatar and view.
IMMUTABLE = "public, max-age=31536000, immutable"


#: Everything below these prefixes is served as a file or as JSON, never as the shell.
NON_SPA_PREFIXES = (
    API_PREFIX,
    "/css/",
    "/js/",
    "/vendor/",
    "/icons/",
    "/media/",
    "/api/docs",
    "/favicon.ico",
)


#: Browsers must not keep an old bundle after an update. "no-cache" still allows a
#: cheap 304, it only forbids using the copy without asking.
REVALIDATE = "no-cache"


class RevalidatingStaticFiles(StaticFiles):
    """Static files that are always revalidated.

    Without an explicit ``Cache-Control`` browsers fall back to heuristic caching and
    may serve a stale script for hours — after an update that means old code talking to
    a new API.
    """

    def file_response(self, *args: object, **kwargs: object) -> Response:
        response = super().file_response(*args, **kwargs)  # type: ignore[arg-type]
        response.headers.setdefault("Cache-Control", REVALIDATE)
        return response


def mount_frontend(app: FastAPI) -> None:
    """Serve the single-page app: assets from their folder, everything else index.html."""
    for folder in ("css", "js", "vendor", "icons"):
        app.mount(
            f"/{folder}",
            RevalidatingStaticFiles(directory=STATIC_DIR / folder),
            name=f"static-{folder}",
        )

    index_file = STATIC_DIR / "index.html"

    @app.get("/media/{name}", include_in_schema=False)
    def media(name: str, current_user: CurrentUser, settings: SettingsDep) -> FileResponse:
        """Avatars and household pictures. Signed in only — they belong to a household."""
        return FileResponse(
            resolve_image(name, settings.media_dir),
            media_type="image/webp",
            headers={"Cache-Control": IMMUTABLE},
        )

    @app.get("/manifest.webmanifest", include_in_schema=False)
    def manifest() -> FileResponse:
        """Makes "add to home screen" install the app in its own window."""
        return FileResponse(
            STATIC_DIR / "manifest.webmanifest",
            media_type="application/manifest+json",
            headers={"Cache-Control": REVALIDATE},
        )

    # The fallback lives in the 404 handler rather than in a catch-all route: a route
    # would shadow everything registered after it.
    @app.exception_handler(StarletteHTTPException)
    async def _spa_fallback(request: Request, exc: StarletteHTTPException) -> Response:
        """A reload on /chores must return the shell, not a 404."""
        wants_page = exc.status_code == 404 and request.method in {"GET", "HEAD"}
        if wants_page and not request.url.path.startswith(NON_SPA_PREFIXES):
            return FileResponse(index_file, headers={"Cache-Control": REVALIDATE})
        return http_exception_response(exc)


def announce_transport_security(settings: Settings) -> None:
    """Say in the log what protects this instance — especially when nothing does."""
    if settings.tls_mode is TlsMode.OFF:
        logger.warning(
            "TLS_MODE=off: this instance serves plain HTTP. Session cookies lose their "
            "Secure flag. Only run it this way behind a TLS reverse proxy or inside a VPN."
        )
        return
    certificate, _ = ensure_certificate(settings) or (None, None)
    logger.info("TLS enabled (%s), certificate: %s", settings.tls_mode, certificate)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Background work that lives as long as the server does.

    The scheduler runs in the application process on purpose: a household instance
    should not need a second container to notice that a chore is due. In `custom` TLS
    mode a second task watches the certificate files for a replacement.
    """
    settings: Settings = app.state.settings
    # Deliberately here and not in create_app(): importing the module must not touch the
    # data volume, and a certificate is only needed once a server actually starts.
    announce_transport_security(settings)

    tasks = [asyncio.create_task(run_scheduler(get_session_factory()))]
    if settings.tls_mode is TlsMode.CUSTOM:
        tasks.append(asyncio.create_task(watch_certificate(settings)))
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(
        lifespan=lifespan,
        title="Kehrwoche",
        version=__version__,
        # Both are served by our own routes below, because the specification limits the
        # reference to signed-in admins.
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
    )
    app.state.settings = settings
    # One set of counters per application instance — no shared state between tests.
    app.state.rate_limiters = RateLimiters.create()
    # Sign-in and registration happen before a CSRF token exists.
    # Outermost, so every answer carries them — errors and static files included.
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CsrfMiddleware,
        settings=settings,
        exempt_paths=[f"{API_PREFIX}/auth/login", f"{API_PREFIX}/auth/register"],
    )
    register_exception_handlers(app)

    @app.get(f"{API_PREFIX}/openapi.json", include_in_schema=False)
    def openapi_document(current_user: AdminUser) -> dict[str, Any]:
        """The API reference — visible to signed-in admins only, as specified."""
        return app.openapi()

    app.include_router(system_router, prefix=API_PREFIX)
    app.include_router(auth_router.router, prefix=API_PREFIX)
    app.include_router(household_router.router, prefix=API_PREFIX)
    app.include_router(chores_router.router, prefix=API_PREFIX)
    app.include_router(shopping_router.router, prefix=API_PREFIX)
    app.include_router(expenses_router.router, prefix=API_PREFIX)
    app.include_router(feed_router.router, prefix=API_PREFIX)
    app.include_router(notifications_router.router, prefix=API_PREFIX)
    app.include_router(users_router.router, prefix=API_PREFIX)
    mount_frontend(app)
    return app


app = create_app()
