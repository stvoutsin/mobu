"""The monkey."""

__all__ = [
    "Monkey",
]

import asyncio
import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from tempfile import NamedTemporaryFile
from typing import IO

import structlog
from aiohttp import ClientSession
from aiojobs import Scheduler
from aiojobs._job import Job
from structlog._config import BoundLoggerLazyProxy

from mobu.business import Business
from mobu.config import Configuration
from mobu.user import User

DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


@dataclass
class Monkey:
    user: User
    log: BoundLoggerLazyProxy
    business: Business
    restart: bool
    state: str

    _job: Job
    _logfile: IO[bytes]

    def __init__(self, user: User):
        self.state = "IDLE"
        self.user = user

        self._logfile = NamedTemporaryFile()

        formatter = logging.Formatter(
            fmt="%(asctime)s %(message)s", datefmt=DATE_FORMAT
        )

        fileHandler = logging.FileHandler(self._logfile.name)
        fileHandler.setFormatter(formatter)

        streamHandler = logging.StreamHandler(stream=sys.stdout)
        streamHandler.setFormatter(formatter)

        logger = logging.getLogger(self.user.username)
        logger.handlers.clear()
        logger.setLevel(logging.INFO)
        logger.addHandler(fileHandler)
        logger.addHandler(streamHandler)
        logger.info(f"Starting new file logger {self._logfile.name}")
        self.log = structlog.wrap_logger(logger)

    def alert(self, msg: str) -> None:
        try:
            time = datetime.now().strftime(DATE_FORMAT)
            alert_msg = f"{time} {self.user.username} {msg}"
            self.log.error(f"Slack Alert: {alert_msg}")
            if Configuration.alert_hook != "None":
                session = ClientSession()
                session.post(
                    Configuration.alert_hook, data={"text": alert_msg}
                )
        except Exception:
            self.log.exception("Exception thrown while trying to alert!")

    def logfile(self) -> str:
        self._logfile.flush()
        return self._logfile.name

    async def start(self, scheduler: Scheduler) -> None:
        self._job = await scheduler.spawn(self._runner())

    async def _runner(self) -> None:
        run = True
        while run:
            try:
                self.state = "RUNNING"
                await self.business.run()
                self.state = "FINISHED"
            except asyncio.CancelledError:
                self.log.info("Shutting down")
                run = False
            except Exception as e:
                self.state = "ERROR"
                self.log.exception(
                    "Exception thrown while doing monkey business."
                )
                # Just pass the exception message - the callstack will
                # be logged but will probably be too spammy to report.
                self.alert(str(e))
                run = self.restart
                await asyncio.sleep(60)

    async def stop(self) -> None:
        try:
            await self._job.close(timeout=0)
        except asyncio.TimeoutError:
            # Close will normally wait for a timeout to occur before
            # throwing a timeout exception, but we'll just shut it down
            # right away and eat the exception.
            pass

    def dump(self) -> dict:
        return {
            "user": self.user.dump(),
            "business": self.business.dump(),
            "state": self.state,
            "restart": self.restart,
        }
