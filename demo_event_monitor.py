"""Standalone, synthetic event-monitoring portfolio demonstration.

Run: python demo_event_monitor.py
No private source, real market feeds, network access, or alert delivery.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import math
import os
import sqlite3


@dataclass(frozen=True)
class Reading:
    timestamp: datetime
    value: float


def _utc_timestamp(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("A timezone-aware datetime is required")
    return value.astimezone(timezone.utc)


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric (excluding bool)")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


class EventMonitor:
    """Store validated observations and unique synthetic threshold alerts.

    Defaults to in-memory SQLite. Supplying a file path enables restart tests.
    One process, ordered inputs; not an actual alert-delivery service.
    """

    def __init__(
        self,
        threshold: float = 80.0,
        cooldown_seconds: int = 600,
        database_path: str | os.PathLike[str] = ":memory:",
    ):
        self.threshold = _finite_number(threshold, "threshold")
        if type(cooldown_seconds) is not int or cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be a nonnegative integer")
        try:
            self.cooldown = timedelta(seconds=cooldown_seconds)
        except OverflowError as exc:
            raise ValueError("cooldown_seconds is too large") from exc
        self.database = sqlite3.connect(database_path)
        try:
            with self.database:
                self.database.execute(
                    "CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
                self.database.execute(
                    "CREATE TABLE IF NOT EXISTS observations ("
                    "timestamp TEXT PRIMARY KEY, value REAL NOT NULL)"
                )
                self.database.execute(
                    "CREATE TABLE IF NOT EXISTS events ("
                    "timestamp TEXT PRIMARY KEY, value REAL NOT NULL)"
                )
                requested = {
                    "threshold": repr(self.threshold),
                    "cooldown_seconds": str(cooldown_seconds),
                }
                stored = dict(self.database.execute("SELECT key, value FROM config"))
                if stored and stored != requested:
                    raise ValueError("Stored event policy differs; use another database")
                if not stored:
                    self.database.executemany(
                        "INSERT INTO config (key, value) VALUES (?, ?)",
                        requested.items(),
                    )
            last_observation = self.database.execute(
                "SELECT MAX(timestamp) FROM observations"
            ).fetchone()[0]
            last_event = self.database.execute(
                "SELECT MAX(timestamp) FROM events"
            ).fetchone()[0]
            self._last_timestamp = (
                datetime.fromisoformat(last_observation) if last_observation else None
            )
            self._last_alert = datetime.fromisoformat(last_event) if last_event else None
        except Exception:
            self.database.close()
            raise

    def process(self, reading: Reading) -> bool:
        """Return True only after atomically storing a newly emitted alert."""
        if not isinstance(reading, Reading):
            raise ValueError("Expected a Reading")
        stamp = _utc_timestamp(reading.timestamp)
        value = _finite_number(reading.value, "reading value")
        if self._last_timestamp is not None and stamp < self._last_timestamp:
            raise ValueError("Readings must arrive in timestamp order")

        stamp_text = stamp.isoformat(timespec="microseconds")
        existing = self.database.execute(
            "SELECT value FROM observations WHERE timestamp = ?", (stamp_text,)
        ).fetchone()
        if existing is not None:
            if existing[0] != value:
                raise ValueError("Conflicting readings share a timestamp")
            return False

        emit = value >= self.threshold and (
            self._last_alert is None or stamp - self._last_alert >= self.cooldown
        )
        # Both writes happen in one transaction. If event insertion fails,
        # the observation also rolls back; in-memory state updates afterward.
        with self.database:
            self.database.execute(
                "INSERT INTO observations (timestamp, value) VALUES (?, ?)",
                (stamp_text, value),
            )
            if emit:
                self.database.execute(
                    "INSERT INTO events (timestamp, value) VALUES (?, ?)",
                    (stamp_text, value),
                )
        self._last_timestamp = stamp
        if emit:
            self._last_alert = stamp
        return emit

    def alerts(self) -> list[dict[str, str | float]]:
        rows = self.database.execute(
            "SELECT timestamp, value FROM events ORDER BY timestamp"
        ).fetchall()
        return [{"timestamp": stamp, "value": value} for stamp, value in rows]

    def close(self) -> None:
        self.database.close()


def main() -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    readings = [
        Reading(start, 55.0),
        Reading(start + timedelta(minutes=1), 84.0),
        Reading(start + timedelta(minutes=1), 84.0),
        Reading(start + timedelta(minutes=3), 92.0),
        Reading(start + timedelta(minutes=12), 90.0),
    ]
    monitor = EventMonitor()
    try:
        emitted = sum(monitor.process(reading) for reading in readings)
        print(json.dumps({
            "synthetic": True,
            "input_records": len(readings),
            "alerts_emitted": emitted,
            "stored_events": monitor.alerts(),
        }, indent=2))
    finally:
        monitor.close()


if __name__ == "__main__":
    main()
