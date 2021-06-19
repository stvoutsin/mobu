from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional


class StopwatchAlreadyStopped(Exception):
    pass


@dataclass
class Stopwatch:
    """A metric container for time data and its serialization.  Create it with
    start(), stop it with stop().  It will fill out its elapsed field on
    stop().

    Give it an event (an arbitrary string) and an optional annotation
    (an arbitrary dict)."""

    start_time: datetime
    stop_time: Optional[datetime] = field(init=False, default=None)
    previous: Optional["Stopwatch"]
    elapsed: timedelta = field(init=False, default=timedelta(0))
    elapsed_since_previous_stop: timedelta = field(
        init=False, default=timedelta(0)
    )
    event: str
    annotation: dict

    @classmethod
    def start(
        cls,
        event: str,
        annotation: dict = {},
        previous: Optional["Stopwatch"] = None,
    ) -> "Stopwatch":
        now = datetime.now(timezone.utc)
        return cls(
            start_time=now,
            event=event,
            annotation=annotation,
            previous=previous,
        )

    def __post_init__(self) -> None:
        if self.previous and self.previous.stop_time:
            self.elapsed_since_previous_stop = (
                self.start_time - self.previous.stop_time
            )

    def stop(self) -> None:
        if self.stop_time:
            raise StopwatchAlreadyStopped(
                f"Stopwatch already stopped at {self.stop_time.isoformat()}"
            )
        now = datetime.now(timezone.utc)
        self.stop_time = now
        self.elapsed = now - self.start_time

    def dump(self) -> Dict[str, Any]:
        """You can't directly JSON-dump datetimes/timedeltas.  So instead
        we convert the time to its ISO 8601 format.  This can be converted
        back to a timestamp with datetime.fromisoformat().

        Likewise, the elapsed time is a float representing number of
        seconds, which you can just pass to a timedelta constructor.
        """
        stopstr: Optional[str] = None
        if self.stop_time is not None:
            stopstr = self.stop_time.isoformat()
        return {
            "event": self.event,
            "annotation": self.annotation,
            "start": self.start_time.isoformat(),
            "stop": stopstr,
            "elapsed": self.elapsed.total_seconds(),
            "previous": self.previous,
            "elapsed_since_previous_stop": self.elapsed_since_previous_stop.total_seconds(),  # noqa: E501
        }
