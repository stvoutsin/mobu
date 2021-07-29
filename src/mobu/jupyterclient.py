"""AsyncIO client for communicating with Jupyter.

Allows the caller to login to the hub, spawn lab containers, and then run
jupyter kernels remotely.
"""

from __future__ import annotations

import asyncio
import random
import re
import string
from dataclasses import dataclass
from http.cookies import BaseCookie
from typing import TYPE_CHECKING
from uuid import uuid4

from aiohttp import (
    ClientResponse,
    ClientSession,
    ClientWebSocketResponse,
    TCPConnector,
)

from .config import config
from .exceptions import NotebookException

if TYPE_CHECKING:
    from typing import Any, Optional

    from aiohttp.client import _RequestContextManager, _WSRequestContextManager
    from structlog import BoundLogger

    from .models.business import BusinessConfig
    from .models.user import AuthenticatedUser

__all__ = ["JupyterClient"]


@dataclass(frozen=True)
class JupyterLabSession:
    """This holds the information a client needs to talk to the Lab in order
    to execute code."""

    session_id: str
    kernel_id: str
    websocket: Optional[ClientWebSocketResponse]


class JupyterClientSession:
    """Wrapper around `aiohttp.ClientSession` using token authentication.

    Unfortunately, aioresponses does not capture headers set on the session
    instead of with each individual call, which means that we can't see the
    token and thus determine what user we should interact with in the test
    suite.  Work around this with this wrapper class around
    `aiohttp.ClientSession` that just adds the token header to every call.

    Parameters
    ----------
    session : `aiohttp.ClientSession`
        The session to wrap.
    token : `str`
        The token to send.
    """

    def __init__(self, session: ClientSession, token: str) -> None:
        self._session = session
        self._token = token

    async def close(self) -> None:
        await self._session.close()

    def request(
        self, method: str, url: str, **kwargs: Any
    ) -> _RequestContextManager:
        if "headers" not in kwargs:
            kwargs["headers"] = {}
        kwargs["headers"]["Authorization"] = f"Bearer {self._token}"
        return self._session.request(method, url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> _RequestContextManager:
        return self.request("delete", url, **kwargs)

    def get(self, url: str, **kwargs: Any) -> _RequestContextManager:
        return self.request("get", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> _RequestContextManager:
        return self.request("post", url, **kwargs)

    def ws_connect(
        self, *args: Any, **kwargs: Any
    ) -> _WSRequestContextManager:
        if "headers" not in kwargs:
            kwargs["headers"] = {}
        kwargs["headers"]["Authorization"] = f"Bearer {self._token}"
        return self._session.ws_connect(*args, **kwargs)


class JupyterClient:
    """Client for talking to JupyterHub and Jupyter labs.

    Notes
    -----
    This class creates its own `aiohttp.ClientSession` for each instance,
    separate from the one used by the rest of the application so that it can
    add some custom settings.
    """

    def __init__(
        self,
        user: AuthenticatedUser,
        log: BoundLogger,
        business_config: BusinessConfig,
    ) -> None:
        self.user = user
        self.log = log
        self.jupyter_base = business_config.nb_url
        self.jupyter_url = config.environment_url + self.jupyter_base
        self.jupyter_options_form = business_config.jupyter_options_form

        xsrftoken = "".join(
            random.choices(string.ascii_uppercase + string.digits, k=16)
        )
        headers = {"x-xsrftoken": xsrftoken}
        session = ClientSession(
            headers=headers, connector=TCPConnector(limit=10000)
        )
        session.cookie_jar.update_cookies(BaseCookie({"_xsrf": xsrftoken}))
        self.session = JupyterClientSession(session, user.token)

    __ansi_reg_exp = re.compile(r"(\x9B|\x1B\[)[0-?]*[ -\/]*[@-~]")

    @classmethod
    def _ansi_escape(cls, line: str) -> str:
        return cls.__ansi_reg_exp.sub("", line)

    async def close(self) -> None:
        await self.session.close()

    async def hub_login(self) -> None:
        async with self.session.get(self.jupyter_url + "hub/login") as r:
            if r.status != 200:
                await self._raise_error("Error logging into hub", r)

    async def ensure_lab(self) -> None:
        self.log.info("Ensure lab")
        running = await self.is_lab_running()
        if running:
            await self.lab_login()
        else:
            await self.spawn_lab()

    async def lab_login(self) -> None:
        self.log.info("Logging into lab")
        lab_url = self.jupyter_url + f"user/{self.user.username}/lab"
        async with self.session.get(lab_url) as r:
            if r.status != 200:
                await self._raise_error("Error logging into lab", r)

    async def is_lab_running(self) -> bool:
        hub_url = self.jupyter_url + "hub"
        spawn_url = self.jupyter_url + "hub/spawn"
        spawn_pending_url = (
            self.jupyter_url + f"hub/spawn-pending/{self.user.username}"
        )
        lab_url = self.jupyter_url + f"user/{self.user.username}/lab"

        async with self.session.get(hub_url) as r:
            if r.status != 200:
                self.log.error(f"Error {r.status} from {r.url}")
                return False

            if str(r.url) == spawn_url or spawn_pending_url in str(r.url):
                self.log.info("Lab is not currently running")
                return False
            elif str(r.url) == lab_url:
                self.log.info("Lab is currently running")
                return True
            else:
                self.log.warning(
                    f"Going to {hub_url} redirected to unexpected URL {r.url}"
                )
                self.log.info("Assuming lab is not running")
                return False

    async def spawn_lab(self) -> None:
        spawn_url = self.jupyter_url + "hub/spawn"
        hub_url = self.jupyter_url + "hub"
        lab_url = self.jupyter_url + f"user/{self.user.username}/lab"

        # DM-23864: Do a get on the spawn URL even if I don't have to.
        async with self.session.get(spawn_url) as r:
            await r.text()

        async with self.session.post(
            spawn_url, data=self.jupyter_options_form, allow_redirects=False
        ) as r:
            if r.status != 302:
                await self._raise_error("Spawn did not redirect", r)

            redirect_url = (
                self.jupyter_base + f"hub/spawn-pending/{self.user.username}"
            )
            if redirect_url not in r.headers["Location"]:
                await self._raise_error("Spawn didn't redirect to pending", r)

        # Jupyterlab will give up a spawn after 900 seconds, so we shouldn't
        # wait longer than that.
        max_poll_secs = 900
        poll_interval = 15
        retries = max_poll_secs / poll_interval

        while retries > 0:
            async with self.session.get(hub_url) as r:
                if str(r.url) == lab_url:
                    self.log.info(f"Lab spawned, redirected to {r.url}")
                    return

                if not r.ok:
                    await self._raise_error("Error spawning", r)

                self.log.info(f"Still waiting for lab to spawn [{r.status}]")
                retries -= 1
                await asyncio.sleep(poll_interval)

        raise Exception("Giving up waiting for lab to spawn!")

    async def delete_lab(self) -> None:
        if not await self.is_lab_running():
            return

        server_url = (
            self.jupyter_url + f"hub/api/users/{self.user.username}/server"
        )
        self.log.info(f"Deleting lab for {self.user.username} at {server_url}")
        headers = {"Referer": self.jupyter_url + "hub/home"}
        async with self.session.delete(server_url, headers=headers) as r:
            if r.status not in [200, 202, 204]:
                await self._raise_error("Error deleting lab", r)

        # Wait for the lab to actually go away.  If we don't do this, we may
        # try to create a new lab while the old one is still shutting down.
        count = 0
        while await self.is_lab_running() and count < 10:
            self.log.info(f"Waiting for lab deletion ({count}s elapsed)")
            await asyncio.sleep(1)
            count += 1
        if await self.is_lab_running():
            self.log.warning("Giving up on waiting for lab deletion")
        else:
            self.log.info("Lab deleted")

    async def create_labsession(
        self, kernel_name: str = "LSST", notebook_name: Optional[str] = None
    ) -> JupyterLabSession:
        session_url = (
            self.jupyter_url + f"user/{self.user.username}/api/sessions"
        )
        session_type = "notebook" if notebook_name else "console"
        body = {
            "kernel": {"name": kernel_name},
            "name": notebook_name or "(no notebook)",
            "path": uuid4().hex,
            "type": session_type,
        }

        async with self.session.post(session_url, json=body) as r:
            if r.status != 201:
                await self._raise_error("Error creating session", r)

            response = await r.json()
            session_id = response["id"]
            kernel_id = response["kernel"]["id"]
            ws = await self._websocket_connect(kernel_id)
            labsession = JupyterLabSession(
                session_id=session_id, kernel_id=kernel_id, websocket=ws
            )
            self.log.info(f"Created JupyterLabSession {labsession}")
            return labsession

    async def _websocket_connect(
        self, kernel_id: str
    ) -> ClientWebSocketResponse:
        channels_url = (
            self.jupyter_url
            + f"user/{self.user.username}/api/kernels/"
            + f"{kernel_id}/channels"
        )
        self.log.info(f"Attempting WebSocket connection to {channels_url}")
        return await self.session.ws_connect(channels_url)

    async def delete_labsession(self, session: JupyterLabSession) -> None:
        await self.lab_login()
        session_url = (
            self.jupyter_url
            + f"user/{self.user.username}/api/sessions/{session.session_id}"
        )
        async with self.session.delete(
            session_url, raise_for_status=True
        ) as r:
            if r.status != 204:
                self.log.warning(f"Delete session {session}: {r}")
            if session.websocket is not None:
                await session.websocket.close()
            return

    async def run_python(self, session: JupyterLabSession, code: str) -> str:
        if not session.websocket:
            self.log.error("Cannot run_python without a websocket!")
            raise Exception("No WebSocket for code execution: {session}")

        msg_id = uuid4().hex

        msg = {
            "header": {
                "username": self.user.username,
                "version": "5.0",
                "session": session.session_id,
                "msg_id": msg_id,
                "msg_type": "execute_request",
            },
            "parent_header": {},
            "channel": "shell",
            "content": {
                "code": code,
                "silent": False,
                "store_history": False,
                "user_expressions": {},
                "allow_stdin": False,
            },
            "metadata": {},
            "buffers": {},
        }

        await session.websocket.send_json(msg)

        result = ""
        while True:
            r = await session.websocket.receive_json()
            self.log.debug(f"Recieved kernel message: {r}")
            msg_type = r["msg_type"]
            if r["parent_header"]["msg_id"] != msg_id:
                # Ignore messages not intended for us. The web socket is
                # rather chatty with broadcast status messages.
                continue
            if msg_type == "error":
                error_message = "".join(r["content"]["traceback"])
                raise NotebookException(self._ansi_escape(error_message))
            elif msg_type == "stream":
                result += r["content"]["text"]
            elif msg_type == "execute_reply":
                status = r["content"]["status"]
                if status == "ok":
                    return result
                else:
                    raise NotebookException(f"Result status is {status}")

    async def _raise_error(self, msg: str, r: ClientResponse) -> None:
        raise Exception(f"{msg}: {r.status} {r.url}: {r.headers}")
