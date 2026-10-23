
"""Standalone synthetic event-monitoring portfolio demonstration.

Run: python demo_event_monitor.py
No private source code, network access, or real alert delivery.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import math
import sqlite3


@dataclass(frozen=True)
class Reading:
    timestamp: datetime
    value: float


class EventMonitor:
    """Detect synthetic threshold events and store unique alerts."""

    def __init__(self, threshold: float = 80.0, cooldown_seconds: int = 600):
        if not math.isfinite(threshold) or cooldown_seconds < 0:
            raise ValueError("Invalid threshold or cooldown")

        self.threshold = threshold
        self.cooldown = timedelta(seconds=cooldown_seconds)
        self.database = sqlite3.connect(":memory:")
        self.database.execute(
            "CREATE TABLE events (timestamp TEXT NOT NULL, value REAL NOT NULL)"
        )
        self._seen: set[datetime] = set()
        self._last_timestamp: datetime | None = None
        self._last_alert: datetime | None = None

    def process(self, reading: Reading) -> bool:
        """Return True only when a new alert is stored."""
        if reading.timestamp.tzinfo is None or reading.timestamp.utcoffset() is None:
            raise ValueError("A timezone-aware timestamp is required")
        if isinstance(reading.value, bool) or not isinstance(reading.value, (int, float)):
            raise ValueError("Reading must be numeric")
        if not math.isfinite(reading.value):
            raise ValueError("Reading must be finite")
        if self._last_timestamp is not None and reading.timestamp < self._last_timestamp:
            raise ValueError("Readings must arrive in timestamp order")
        if reading.timestamp in self._seen:
            return False

        self._seen.add(reading.timestamp)
        self._last_timestamp = reading.timestamp

        if reading.value < self.threshold:
            return False
        if self._last_alert is not None:
            if reading.timestamp - self._last_alert < self.cooldown:
                return False

        self.database.execute(
            "INSERT INTO events (timestamp, value) VALUES (?, ?)",
            (reading.timestamp.isoformat(), reading.value),
        )
        self.database.commit()
        self._last_alert = reading.timestamp
        return True

    def alerts(self) -> list[dict[str, str | float]]:
        rows = self.database.execute(
            "SELECT timestamp, value FROM events ORDER BY timestamp"
        ).fetchall()
        return [
            {"timestamp": stamp, "value": value}
            for stamp, value in rows
        ]

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
        emitted = sum(monitor.process(r) for r in readings)
        report = {
            "input_records": len(readings),
            "alerts_emitted": emitted,
            "stored_events": monitor.alerts(),
        }
        print(json.dumps(report, indent=2))
    finally:
        monitor.close()


if __name__ == "__main__":
    main()
