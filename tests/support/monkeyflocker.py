"""Create a version of the app for monkeyflocker testing."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import respx
from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from mobu.main import app

from .gafaelfawr import mock_gafaelfawr
from .jupyter import mock_jupyter


class AddAuthHeaderMiddleware(BaseHTTPMiddleware):
    """Mock running behind a Gafaelfawr-aware ingress.

    Pretend Gafaelfawr is doing authentication in front of mobu.  This is a
    total hack based on https://github.com/tiangolo/fastapi/issues/2727 that
    adds the header that would have been added by Gafaelfawr.  Unfortunately,
    there's no documented way to modify request headers in middleware, so we
    have to muck about with internals.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.headers.__dict__["_list"].append(
            (b"x-auth-request-user", b"someuser")
        )
        return await call_next(request)


def create_app() -> FastAPI:
    """Configure the FastAPI app for monkeyflocker testing."""
    app.add_middleware(AddAuthHeaderMiddleware)

    @app.on_event("startup")
    async def startup_event() -> None:
        respx.start()
        mock_gafaelfawr(respx.mock)
        mock_jupyter(respx.mock)

    return app
