from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from math import isfinite
from threading import Lock
from time import monotonic
from typing import Protocol
from zoneinfo import ZoneInfo

DUBAI = ZoneInfo("Asia/Dubai")
DEMO_STARTS: Mapping[str, datetime] = {
    "demo": datetime(2026, 11, 19, 6, tzinfo=UTC),
    "C01": datetime(2026, 11, 19, 6, tzinfo=UTC),
    "C09": datetime(2026, 11, 19, 19, 50, tzinfo=UTC),
}


def as_utc(instant: datetime) -> datetime:
    if instant.utcoffset() is None:
        raise ValueError("a timezone-aware datetime is required")
    return instant.astimezone(UTC)


def in_dubai(instant: datetime) -> datetime:
    return as_utc(instant).astimezone(DUBAI)


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class SimClock:
    def __init__(
        self, start: str | datetime = "demo", *, starts: Mapping[str, datetime] | None = None
    ) -> None:
        self._starts = DEMO_STARTS | (starts or {})
        self.set(start)

    def now(self) -> datetime:
        return self._instant

    def set(self, instant: str | datetime) -> datetime:
        self._instant = as_utc(self._starts[instant] if isinstance(instant, str) else instant)
        return self._instant

    def advance(self, seconds: int) -> datetime:
        if type(seconds) is not int or seconds < 0:
            raise ValueError("advance requires non-negative integer seconds")
        return self.set(self._instant + timedelta(seconds=seconds))


class ReplayClock:
    def __init__(
        self, start: str | datetime, *, speed: float = 1.0,
        starts: Mapping[str, str | datetime] | None = None,
    ) -> None:
        if not isfinite(speed) or speed < 0:
            raise ValueError("speed requires a finite non-negative number")
        self._speed = speed
        self._starts = DEMO_STARTS | (starts or {})
        self._lock = Lock()
        self.set(start)

    def _at(self, tick: float) -> datetime:
        return self._base + timedelta(seconds=(tick - self._anchor) * self._speed)

    def now(self) -> datetime:
        with self._lock:
            return self._at(monotonic())

    def set(self, instant: str | datetime) -> datetime:
        if isinstance(instant, str):
            instant = self._starts.get(instant, instant)
        if isinstance(instant, str):
            instant = datetime.fromisoformat(instant)
        base = as_utc(instant)
        with self._lock:
            self._base, self._anchor = base, monotonic()
        return base

    def advance(self, seconds: int) -> datetime:
        if type(seconds) is not int or seconds < 0:
            raise ValueError("advance requires non-negative integer seconds")
        with self._lock:
            tick = monotonic()
            self._base = self._at(tick) + timedelta(seconds=seconds)
            self._anchor = tick
            return self._base
