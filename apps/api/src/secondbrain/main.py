"""FastAPI application factory and the `secondbrain-api` entrypoint."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from secondbrain import __version__
from secondbrain.api.v1.router import router as v1_router
from secondbrain.config import Settings, get_settings
from secondbrain.db.engine import get_engine

log = logging.getLogger(__name__)


def configure_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=logging.DEBUG if settings.environment == "development" else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    # Third-party loggers that are unreadably chatty at DEBUG; opt in explicitly when needed.
    for name in (
        "sqlalchemy.engine",
        "httpcore",
        "httpx",
        "huggingface_hub",
        "urllib3",
        "filelock",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    log.info("%s v%s starting (%s)", settings.app_name, __version__, settings.environment)
    log.info("data dir: %s", settings.data_dir)
    yield
    get_engine().dispose()
    log.info("shutdown complete")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description="An AI system that builds and maintains a structured model of what you know.",
        lifespan=lifespan,
    )
    app.state.settings = settings

    # The browser (Next.js dev server) calls FastAPI directly — no BFF layer.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(v1_router)
    return app


app = create_app()


def run() -> None:
    """Console entrypoint: `secondbrain-api`. Reload is on in development."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "secondbrain.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.environment == "development",
    )


if __name__ == "__main__":
    run()
