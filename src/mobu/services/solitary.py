"""Manager for a solitary monkey."""

from __future__ import annotations

from pathlib import Path

from httpx import AsyncClient
from structlog.stdlib import BoundLogger

from ..models.solitary import SolitaryConfig, SolitaryResult
from ..models.user import AuthenticatedUser
from .monkey import Monkey

__all__ = ["Solitary"]


class Solitary:
    """Runs a single monkey to completion and reports its results.

    Parameters
    ----------
    solitary_config
        Configuration for the monkey.
    http_client
        Shared HTTP client.
    logger
        Global logger.
    """

    def __init__(
        self,
        solitary_config: SolitaryConfig,
        http_client: AsyncClient,
        logger: BoundLogger,
    ) -> None:
        self._config = solitary_config
        self._http_client = http_client
        self._logger = logger

    async def run(self) -> SolitaryResult:
        """Run the monkey and return its results.

        Returns
        -------
        SolitaryResult
            Result of monkey run.
        """
        user = await AuthenticatedUser.create(
            self._config.user, self._config.scopes, self._http_client
        )
        monkey = Monkey(
            name=f"solitary-{user.username}",
            business_config=self._config.business,
            user=user,
            http_client=self._http_client,
            logger=self._logger,
        )
        error = await monkey.run_once()
        return SolitaryResult(
            success=error is None,
            error=error,
            log=Path(monkey.logfile()).read_text(),
        )
