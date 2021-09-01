"""Test fixtures for mobu tests."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from aiohttp import ClientSession
from aioresponses import aioresponses
from asgi_lifespan import LifespanManager
from httpx import AsyncClient

from mobu import main
from mobu.config import config
from tests.support.cachemachine import mock_cachemachine
from tests.support.gafaelfawr import make_gafaelfawr_token
from tests.support.jupyter import mock_jupyter, mock_jupyter_websocket
from tests.support.slack import mock_slack

if TYPE_CHECKING:
    from typing import Any, AsyncIterator, Iterator

    from fastapi import FastAPI

    from tests.support.cachemachine import MockCachemachine
    from tests.support.jupyter import MockJupyter, MockJupyterWebSocket
    from tests.support.slack import MockSlack


@pytest.fixture(autouse=True)
def configure() -> Iterator[None]:
    """Set minimal configuration settings.

    Add an environment URL for testing purposes and create a Gafaelfawr admin
    token and add it to the configuration.

    This is an autouse fixture, so it will ensure that each test gets the
    minimal test configuration and a unique admin token that is replaced after
    the test runs.
    """
    config.environment_url = "https://test.example.com"
    config.gafaelfawr_token = make_gafaelfawr_token()
    yield
    config.environment_url = ""
    config.gafaelfawr_token = None


@pytest.fixture
async def app(
    jupyter: MockJupyter, cachemachine: MockCachemachine
) -> AsyncIterator[FastAPI]:
    """Return a configured test application.

    Wraps the application in a lifespan manager so that startup and shutdown
    events are sent during test execution.

    Notes
    -----
    This must depend on the Jupyter mock since otherwise the JupyterClient
    mocking is undone before the app is shut down, which causes it to try to
    make real web socket calls.

    A tests in business/jupyterloginloop_test.py depends on the exact shutdown
    timeout.
    """
    async with LifespanManager(main.app, shutdown_timeout=10):
        yield main.app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Return an ``httpx.AsyncClient`` configured to talk to the test app."""
    async with AsyncClient(app=app, base_url="https://example.com/") as client:
        yield client


@pytest.fixture
def mock_aioresponses() -> Iterator[aioresponses]:
    """Set up aioresponses for aiohttp mocking."""
    with aioresponses() as mocked:
        yield mocked


@pytest.fixture
def cachemachine(mock_aioresponses: aioresponses) -> MockCachemachine:
    """Mock out cachemachine."""
    return mock_cachemachine(mock_aioresponses)


@pytest.fixture
def jupyter(mock_aioresponses: aioresponses) -> Iterator[MockJupyter]:
    """Mock out JupyterHub/Lab."""
    jupyter_mock = mock_jupyter(mock_aioresponses)

    # aioresponses has no mechanism to mock ws_connect, so we have to do it
    # ourselves.
    async def mock_ws_connect(url: str, **kwargs: Any) -> MockJupyterWebSocket:
        return mock_jupyter_websocket(url, jupyter_mock)

    with patch.object(ClientSession, "ws_connect") as mock:
        mock.side_effect = mock_ws_connect
        yield jupyter_mock


@pytest.fixture
def slack(mock_aioresponses: aioresponses) -> Iterator[MockSlack]:
    """Mock out Slack for alerting."""
    config.alert_hook = "https://slack.example.com/services/XXXX/YYYYY"
    yield mock_slack(mock_aioresponses)
    config.alert_hook = None
