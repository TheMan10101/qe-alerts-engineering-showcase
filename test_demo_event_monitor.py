
"""Unit tests for the standalone QE Alerts synthetic demo.

Run: python -m unittest discover -v
"""

from datetime import datetime, timedelta, timezone
import unittest

from demo_event_monitor import EventMonitor, Reading

START = datetime(2026, 1, 1, tzinfo=timezone.utc)


class TestEventMonitor(unittest.TestCase):
    def setUp(self):
        self.monitor = EventMonitor(
            threshold=80.0, cooldown_seconds=600
        )

    def tearDown(self):
        self.monitor.close()

    def reading(self, minutes, value):
        return Reading(
            START + timedelta(minutes=minutes), value
        )

    def test_below_threshold_no_alert(self):
        self.assertFalse(
            self.monitor.process(self.reading(0, 79.9))
        )
        self.assertEqual(self.monitor.alerts(), [])

    def test_threshold_boundary_triggers(self):
        self.assertTrue(
            self.monitor.process(self.reading(0, 80.0))
        )
        self.assertEqual(len(self.monitor.alerts()), 1)

    def test_duplicate_timestamp_is_suppressed(self):
        reading = self.reading(0, 90.0)
        self.assertTrue(self.monitor.process(reading))
        self.assertFalse(self.monitor.process(reading))
        self.assertEqual(len(self.monitor.alerts()), 1)

    def test_cooldown_suppresses_early_alert(self):
        self.assertTrue(
            self.monitor.process(self.reading(0, 90.0))
        )
        self.assertFalse(
            self.monitor.process(self.reading(9, 91.0))
        )
        self.assertEqual(len(self.monitor.alerts()), 1)

    def test_exact_cooldown_boundary_allows_alert(self):
        self.assertTrue(
            self.monitor.process(self.reading(0, 90.0))
        )
        self.assertTrue(
            self.monitor.process(self.reading(10, 91.0))
        )
        self.assertEqual(len(self.monitor.alerts()), 2)

    def test_synthetic_sequence_emits_two_alerts(self):
        readings = [
            self.reading(0, 55.0),
            self.reading(1, 84.0),
            self.reading(1, 84.0),
            self.reading(3, 92.0),
            self.reading(12, 90.0),
        ]
        emitted = sum(
            self.monitor.process(r) for r in readings
        )
        self.assertEqual(emitted, 2)
        self.assertEqual(len(self.monitor.alerts()), 2)

    def test_sqlite_records_timestamp_and_value(self):
        reading = self.reading(2, 88.5)
        self.assertTrue(self.monitor.process(reading))
        self.assertEqual(
            self.monitor.alerts(),
            [{
                "timestamp": reading.timestamp.isoformat(),
                "value": 88.5,
            }],
        )

    def test_rejects_out_of_order_readings(self):
        self.monitor.process(self.reading(5, 90.0))
        with self.assertRaises(ValueError):
            self.monitor.process(self.reading(4, 91.0))

    def test_rejects_naive_timestamp(self):
        naive = Reading(datetime(2026, 1, 1), 90.0)
        with self.assertRaises(ValueError):
            self.monitor.process(naive)

    def test_rejects_invalid_values(self):
        bad = (True, "90", float("nan"), float("inf"))
        for value in bad:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.monitor.process(
                        self.reading(0, value)
                    )
        self.assertEqual(self.monitor.alerts(), [])

    def test_rejects_invalid_configuration(self):
        configurations = (
            (float("nan"), 600),
            (80.0, -1),
        )
        for threshold, cooldown in configurations:
            with self.subTest(
                threshold=threshold, cooldown=cooldown
            ):
                with self.assertRaises(ValueError):
                    EventMonitor(
                        threshold=threshold,
                        cooldown_seconds=cooldown,
                    )


if __name__ == "__main__":
    unittest.main()
