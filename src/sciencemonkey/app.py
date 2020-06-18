"""The main application definition for sciencemonkey service."""

__all__ = ["create_app"]

from aiohttp import web
from aiojobs.aiohttp import setup
from safir.http import init_http_session
from safir.logging import configure_logging
from safir.metadata import setup_metadata
from safir.middleware import bind_logger

from sciencemonkey.config import Configuration
from sciencemonkey.handlers import init_external_routes, init_internal_routes
from sciencemonkey.monkeybusinessmanager import MonkeyBusinessManager


def create_app() -> web.Application:
    """Create and configure the aiohttp.web application."""
    config = Configuration()
    configure_logging(
        profile=config.profile,
        log_level=config.log_level,
        name=config.logger_name,
    )

    root_app = web.Application()
    root_app["safir/config"] = config
    setup_metadata(package_name="sciencemonkey", app=root_app)
    setup_middleware(root_app)
    root_app.add_routes(init_internal_routes())
    root_app.cleanup_ctx.append(init_http_session)

    sub_app = web.Application()

    manager = MonkeyBusinessManager()
    root_app["sciencemonkey/monkeybusinessmanager"] = manager
    root_app.on_startup.append(manager.init)
    root_app.on_cleanup.append(manager.cleanup)
    setup_middleware(sub_app)
    setup(sub_app)
    sub_app.add_routes(init_external_routes())

    root_app.add_subapp(f'/{root_app["safir/config"].name}', sub_app)
    return root_app


def setup_middleware(app: web.Application) -> None:
    """Add middleware to the application."""
    app.middlewares.append(bind_logger)
