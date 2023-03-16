"""A flock of monkeys doing business."""

from __future__ import annotations

import asyncio
import math
from datetime import datetime
from typing import Optional

from aiohttp import ClientSession
from aiojobs import Scheduler
from safir.datetime import current_datetime

from .business.base import Business
from .business.jupyterjitterloginloop import JupyterJitterLoginLoop
from .business.jupyterloginloop import JupyterLoginLoop
from .business.jupyterpythonloop import JupyterPythonLoop
from .business.notebookrunner import NotebookRunner
from .business.tapqueryrunner import TAPQueryRunner
from .exceptions import MonkeyNotFoundException
from .models.flock import FlockConfig, FlockData, FlockSummary
from .models.user import AuthenticatedUser, User, UserSpec
from .monkey import Monkey

__all__ = ["Flock"]

_BUSINESS_CLASS = {
    "Business": Business,
    "JupyterJItterLoginLoop": JupyterJitterLoginLoop,
    "JupyterLoginLoop": JupyterLoginLoop,
    "JupyterPythonLoop": JupyterPythonLoop,
    "NotebookRunner": NotebookRunner,
    "TAPQueryRunner": TAPQueryRunner,
}


class Flock:
    """Container for a group of monkeys all running the same business."""

    def __init__(
        self,
        flock_config: FlockConfig,
        scheduler: Scheduler,
        session: ClientSession,
    ) -> None:
        self.name = flock_config.name
        self._config = flock_config
        self._scheduler = scheduler
        self._session = session
        self._monkeys: dict[str, Monkey] = {}
        self._start_time: Optional[datetime] = None
        try:
            self._business_type = _BUSINESS_CLASS[self._config.business]
        except ValueError:
            raise ValueError(f"Unknown business {self._config.business}")

    def dump(self) -> FlockData:
        """Return information about all running monkeys."""
        return FlockData(
            name=self._config.name,
            config=self._config,
            monkeys=[m.dump() for m in self._monkeys.values()],
        )

    def get_monkey(self, name: str) -> Monkey:
        """Retrieve a given monkey by name."""
        monkey = self._monkeys.get(name)
        if not monkey:
            raise MonkeyNotFoundException(name)
        return monkey

    def list_monkeys(self) -> list[str]:
        """List the names of the monkeys."""
        return sorted(self._monkeys.keys())

    def summary(self) -> FlockSummary:
        """Return summary statistics about the flock."""
        count = 0
        successes = 0
        failures = 0
        for monkey in self._monkeys.values():
            count += 1
            successes += monkey.business.success_count
            failures += monkey.business.failure_count
        return FlockSummary(
            name=self.name,
            business=self._config.business,
            start_time=self._start_time,
            monkey_count=count,
            success_count=successes,
            failure_count=failures,
        )

    async def start(self) -> None:
        """Start all the monkeys."""
        users = await self._create_users()
        for user in users:
            monkey = self._create_monkey(user)
            self._monkeys[user.username] = monkey
            await monkey.start(self._scheduler)
        self._start_time = current_datetime(microseconds=True)

    async def stop(self) -> None:
        """Stop all the monkeys.

        Stopping a monkey can require waiting for a timeout from JupyterHub if
        it were in the middle of spawning, so stop them all in parallel to
        avoid waiting for the sum of all timeouts.
        """
        awaits = [m.stop() for m in self._monkeys.values()]
        await asyncio.gather(*awaits)

    def _create_monkey(self, user: AuthenticatedUser) -> Monkey:
        """Create a monkey that will run as a given user."""
        config = self._config.monkey_config(user.username)
        return Monkey(config, self._business_type, user, self._session)

    async def _create_users(self) -> list[AuthenticatedUser]:
        """Create the authenticated users the monkeys will run as."""
        users = self._config.users
        if not users:
            assert self._config.user_spec
            count = self._config.count
            users = self._users_from_spec(self._config.user_spec, count)
        scopes = self._config.scopes
        return [
            await AuthenticatedUser.create(u, scopes, self._session)
            for u in users
        ]

    def _users_from_spec(self, spec: UserSpec, count: int) -> list[User]:
        """Generate count Users from the provided spec."""
        padding = int(math.log10(count) + 1)
        users = []
        for i in range(1, count + 1):
            username = spec.username_prefix + str(i).zfill(padding)
            if spec.uid_start is not None:
                uid = spec.uid_start + i - 1
            else:
                uid = None
            if spec.gid_start is not None:
                gid = spec.gid_start + i - 1
            else:
                gid = None
            user = User(username=username, uidnumber=uid, gidnumber=gid)
            users.append(user)
        return users
