"""Exceptions for mobu."""

from __future__ import annotations

from abc import ABCMeta, abstractmethod
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from aiohttp import ClientResponseError

from .constants import DATE_FORMAT

if TYPE_CHECKING:
    from typing import Any, Dict, List, Optional

    from aiohttp import ClientResponse

__all__ = [
    "CodeExecutionError",
    "FlockNotFoundException",
    "JupyterError",
    "JupyterTimeoutError",
    "MonkeyNotFoundException",
    "SlackError",
]


class FlockNotFoundException(Exception):
    """The named flock was not found."""

    def __init__(self, flock: str) -> None:
        self.flock = flock
        super().__init__(f"Flock {flock} not found")


class MonkeyNotFoundException(Exception):
    """The named monkey was not found."""

    def __init__(self, monkey: str) -> None:
        self.monkey = monkey
        super().__init__(f"Monkey {monkey} not found")


class NotebookRepositoryError(Exception):
    """The repository containing notebooks to run is not valid."""


class SlackError(Exception, metaclass=ABCMeta):
    """Represents an exception that can be reported to Slack.

    Intended to be subclassed.  Subclasses must override the to_slack
    method.
    """

    def __init__(self, user: str, msg: str) -> None:
        self.user = user
        self.failed = datetime.now(tz=timezone.utc)
        self.started: Optional[datetime] = None
        self.event: Optional[str] = None
        self.annotations: Optional[Dict[str, str]] = None
        super().__init__(msg)

    @abstractmethod
    def to_slack(self) -> Dict[str, Any]:
        """Build a Slack message suitable for sending to an incoming webook."""

    def common_fields(self) -> List[Dict[str, str]]:
        """Return common fields to put in any alert."""
        failed = self.failed.strftime(DATE_FORMAT)
        fields = [
            {"type": "mrkdwn", "text": f"*Failed at*\n{failed}"},
            {"type": "mrkdwn", "text": f"*User*\n{self.user}"},
        ]
        if self.started:
            started = self.started.strftime(DATE_FORMAT)
            fields.insert(
                0, {"type": "mrkdwn", "text": f"*Started at*\n{started}"}
            )
        if self.event:
            fields.append({"type": "mrkdwn", "text": f"*Event*\n{self.event}"})
        return fields


class CodeExecutionError(SlackError):
    """Error generated by code execution in a notebook on JupyterLab."""

    def __init__(
        self,
        user: str,
        code: str,
        *,
        error: Optional[str] = None,
        notebook: Optional[str] = None,
        status: Optional[str] = None,
        annotations: Optional[Dict[str, str]] = None,
    ) -> None:
        self.code = code
        self.error = error
        self.notebook = notebook
        self.status = status
        self.annotations = annotations if annotations else {}
        super().__init__(user, "Code execution failed")

    def __str__(self) -> str:
        if self.notebook:
            message = f"{self.user}: cell of notebook {self.notebook} failed"
            if self.status:
                message += f" (status: {self.status})"
            message += f"\nCode: {self.code}"
        else:
            message = f"{self.user}: running code '{self.code}' block failed"
        message += f"\nError: {self.error}"
        return message

    def to_slack(self) -> Dict[str, Any]:
        """Format the error as a Slack Block Kit message."""
        if self.notebook:
            intro = f"Error while running `{self.notebook}`"
        else:
            intro = "Error while running code"
        if self.status:
            intro += f"\n*Status*: {self.status}"

        fields = self.common_fields()
        if self.annotations and self.annotations.get("node"):
            node = self.annotations["node"]
            fields.append({"type": "mrkdwn", "text": f"*Node*\n{node}"})

        code = self.code
        if not code.endswith("\n"):
            code += "\n"
        result: Dict[str, Any] = {
            "blocks": [
                {"type": "section", "text": {"type": "mrkdwn", "text": intro}},
                {"type": "section", "fields": fields},
            ],
            "attachments": [
                {
                    "blocks": [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"*Code executed*\n```\n{code}```",
                                "verbatim": True,
                            },
                        }
                    ],
                }
            ],
        }
        if self.error:
            error = self.error
            if error and not error.endswith("\n"):
                error += "\n"
            result["attachments"][0]["blocks"].insert(
                0,
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Error*\n```\n{error}```",
                        "verbatim": True,
                    },
                },
            )
        return result


class JupyterError(SlackError):
    """Web error from JupyterHub or JupyterLab."""

    @classmethod
    def from_exception(
        cls, user: str, exc: ClientResponseError
    ) -> JupyterError:
        return cls(
            url=str(exc.request_info.url),
            user=user,
            status=exc.status,
            reason=exc.message,
            method=exc.request_info.method,
        )

    @classmethod
    async def from_response(
        cls, user: str, response: ClientResponse
    ) -> JupyterError:
        return cls(
            url=str(response.url),
            user=user,
            status=response.status,
            reason=response.reason,
            method=response.method,
            body=await response.text(),
        )

    def __init__(
        self,
        *,
        url: str,
        user: str,
        status: int,
        reason: Optional[str],
        method: str,
        body: Optional[str] = None,
    ) -> None:
        self.url = url
        self.status = status
        self.reason = reason
        self.method = method
        self.body = body
        super().__init__(user, f"Status {status} from {method} {url}")

    def __str__(self) -> str:
        result = (
            f"{self.user}: status {self.status} ({self.reason}) from"
            f" {self.method} {self.url}"
        )
        if self.body:
            result += f"\nBody:\n{self.body}\n"
        return result

    def to_slack(self) -> Dict[str, Any]:
        """Format the error as a Slack Block Kit message."""
        intro = f"Status {self.status} from {self.method} {self.url}"
        fields = self.common_fields()
        if self.reason:
            fields.append(
                {"type": "mrkdwn", "text": f"*Message*\n{self.reason}"}
            )
        return {
            "blocks": [
                {"type": "section", "text": {"type": "mrkdwn", "text": intro}},
                {"type": "section", "fields": fields},
                {"type": "divider"},
            ]
        }


class JupyterSpawnError(SlackError):
    """The Jupyter Lab pod failed to spawn."""

    def __init__(self, user: str, log: str) -> None:
        super().__init__(user, "Spawning lab failed")
        self.log = log

    def to_slack(self) -> Dict[str, Any]:
        """Format the error as a Slack Block Kit message."""
        return {
            "blocks": [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "Spawning lab failed"},
                },
                {"type": "section", "fields": self.common_fields()},
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Log*\n{self.log}",
                        "verbatim": True,
                    },
                },
                {"type": "divider"},
            ]
        }


class JupyterTimeoutError(SlackError):
    """Timed out waiting for the lab to spawn."""

    def __init__(self, user: str, msg: str, log: Optional[str] = None) -> None:
        super().__init__(user, msg)
        self.log = log

    def to_slack(self) -> Dict[str, Any]:
        """Format the error as a Slack Block Kit message."""
        result = {
            "blocks": [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": str(self)},
                },
                {"type": "section", "fields": self.common_fields()},
            ]
        }
        if self.log:
            result["blocks"].append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Log*\n{self.log}",
                        "verbatim": True,
                    },
                }
            )
        result["blocks"].append({"type": "divider"})
        return result
