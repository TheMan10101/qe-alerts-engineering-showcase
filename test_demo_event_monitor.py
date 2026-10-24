"""Standard-library unit tests for the synthetic QE Alerts example.

Run: python -m unittest discover -v
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

from demo_event_monitor import EventMonitor, Reading

START = datetime(2026, 1, 1, tzinfo=timezone.utc)


class TestEventMonitor(unittest.TestCase):
    def setUp(self):
        self.monitor = EventMonitor(threshold=80.0, cooldown_seconds=600)

    def tearDown(self):
        self.monitor.close()

    def reading(self, minutes, value):
        return Reading(START + timedelta(minutes=minutes), value)

    def test_below_threshold_no_alert(self):
        self.assertFalse(self.monitor.process(self.reading(0, 79.9)))
        self.assertEqual(self.monitor.alerts(), [])

    def test_threshold_boundary_triggers(self):
        self.assertTrue(self.monitor.process(self.reading(0, 80.0)))
        self.assertEqual(len(self.monitor.alerts()), 1)

    def test_exact_duplicate_is_suppressed(self):
        same = self.reading(0, 90.0)
        self.assertTrue(self.monitor.process(same))
        self.assertFalse(self.monitor.process(same))
        self.assertEqual(len(self.monitor.alerts()), 1)

    def test_conflicting_timestamp_rejected_without_state_change(self):
        self.assertTrue(self.monitor.process(self.reading(0, 90.0)))
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            self.monitor.process(self.reading(0, 91.0))
        self.assertEqual([event["value"] for event in self.monitor.alerts()], [90.0])

    def test_below_threshold_observation_is_also_deduplicated(self):
        self.assertFalse(self.monitor.process(self.reading(0, 20.0)))
        self.assertFalse(self.monitor.process(self.reading(0, 20.0)))
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            self.monitor.process(self.reading(0, 95.0))

    def test_cooldown_suppresses_early_alert(self):
        self.assertTrue(self.monitor.process(self.reading(0, 90.0)))
        self.assertFalse(self.monitor.process(self.reading(9, 91.0)))
        self.assertEqual(len(self.monitor.alerts()), 1)

    def test_exact_cooldown_boundary_allows_alert(self):
        self.assertTrue(self.monitor.process(self.reading(0, 90.0)))
        self.assertTrue(self.monitor.process(self.reading(10, 91.0)))
        self.assertEqual(len(self.monitor.alerts()), 2)

    def test_zero_cooldown_allows_every_new_qualifying_time(self):
        monitor = EventMonitor(cooldown_seconds=0)
        try:
            self.assertTrue(monitor.process(self.reading(0, 90.0)))
            self.assertTrue(monitor.process(self.reading(1, 91.0)))
        finally:
            monitor.close()

    def test_synthetic_sequence_emits_two_alerts(self):
        readings = [
            self.reading(0, 55.0), self.reading(1, 84.0),
            self.reading(1, 84.0), self.reading(3, 92.0),
            self.reading(12, 90.0),
        ]
        self.assertEqual(sum(self.monitor.process(r) for r in readings), 2)
        self.assertEqual(len(self.monitor.alerts()), 2)

    def test_sqlite_records_utc_timestamp_and_value(self):
        value = self.reading(2, 88.5)
        self.assertTrue(self.monitor.process(value))
        self.assertEqual(self.monitor.alerts(), [{
            "timestamp": value.timestamp.isoformat(timespec="microseconds"),
            "value": 88.5,
        }])

    def test_rejects_out_of_order_readings(self):
        self.monitor.process(self.reading(5, 90.0))
        with self.assertRaises(ValueError):
            self.monitor.process(self.reading(4, 91.0))

    def test_rejects_naive_timestamp(self):
        with self.assertRaises(ValueError):
            self.monitor.process(Reading(datetime(2026, 1, 1), 90.0))

    def test_rejects_timestamp_wrong_type(self):
        with self.assertRaises(ValueError):
            self.monitor.process(Reading("2026-01-01", 90.0))

    def test_rejects_non_reading(self):
        with self.assertRaises(ValueError):
            self.monitor.process({"timestamp": START, "value": 90.0})

    def test_rejects_invalid_values(self):
        for value in (True, "90", None, float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.monitor.process(self.reading(0, value))
        self.assertEqual(self.monitor.alerts(), [])

    def test_rejects_invalid_configuration(self):
        bad_settings = [
            (float("nan"), 600), (float("inf"), 600), (True, 600),
            ("80", 600), (80, -1), (80, 1.5), (80, True), (80, "600"),
            (80, 10 ** 30),
        ]
        for threshold, cooldown in bad_settings:
            with self.subTest(threshold=threshold, cooldown=cooldown):
                with self.assertRaises(ValueError):
                    EventMonitor(threshold=threshold, cooldown_seconds=cooldown)

    def test_equivalent_timezone_instants_are_duplicates(self):
        same_in_calgary_offset = START.astimezone(timezone(timedelta(hours=-6)))
        self.assertTrue(self.monitor.process(Reading(START, 90.0)))
        self.assertFalse(self.monitor.process(Reading(same_in_calgary_offset, 90.0)))
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            self.monitor.process(Reading(same_in_calgary_offset, 91.0))

    def test_file_database_recovers_history_and_cooldown(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "demo.sqlite"
            first = EventMonitor(database_path=path)
            try:
                self.assertTrue(first.process(self.reading(0, 90.0)))
                self.assertFalse(first.process(self.reading(5, 92.0)))
            finally:
                first.close()
            resumed = EventMonitor(database_path=path)
            try:
                self.assertFalse(resumed.process(self.reading(5, 92.0)))
                self.assertFalse(resumed.process(self.reading(9, 93.0)))
                self.assertTrue(resumed.process(self.reading(10, 94.0)))
                self.assertEqual(len(resumed.alerts()), 2)
            finally:
                resumed.close()

    def test_refuses_changed_policy_for_existing_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "demo.sqlite"
            monitor = EventMonitor(database_path=path)
            monitor.close()
            with self.assertRaisesRegex(ValueError, "policy differs"):
                EventMonitor(threshold=81.0, database_path=path)
            with self.assertRaisesRegex(ValueError, "policy differs"):
                EventMonitor(cooldown_seconds=1, database_path=path)

    def test_transaction_failure_rolls_back_observation(self):
        self.monitor.database.execute(
            "CREATE TEMP TRIGGER block_event BEFORE INSERT ON events "
            "BEGIN SELECT RAISE(ABORT, 'synthetic injected failure'); END"
        )
        with self.assertRaises(sqlite3.DatabaseError):
            self.monitor.process(self.reading(0, 90.0))
        count = self.monitor.database.execute(
            "SELECT COUNT(*) FROM observations"
        ).fetchone()[0]
        self.assertEqual(count, 0)
        self.assertEqual(self.monitor.alerts(), [])
        self.monitor.database.execute("DROP TRIGGER block_event")
        self.assertTrue(self.monitor.process(self.reading(0, 90.0)))

    def test_initial_state_has_no_events(self):
        self.assertEqual(self.monitor.alerts(), [])


if __name__ == "__main__":
    unittest.main()
