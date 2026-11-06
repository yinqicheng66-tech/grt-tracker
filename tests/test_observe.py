import unittest

from pipeline.db import connect
from pipeline.gtfsrt import parse_feed
from pipeline.observe import Pending, Tracker, departure_time
from pipeline.timetable import timetable_time
from tests import pb

DATE = "20261105"
T0 = timetable_time(DATE, "12:00:00")


def snapshot(ts, trips):
    """trips: list of (trip_id, [(seq, stop_id, predicted)], tracked)."""
    updates = [
        pb.trip_update(
            pb.trip(trip_id, start_date=DATE),
            [pb.stop_update(seq, stop, time=t) for seq, stop, t in stops],
            timestamp=ts if tracked else None,
        )
        for trip_id, stops, tracked in trips
    ]
    return parse_feed(pb.feed(ts, trip_updates=updates))


class DepartureTimeTest(unittest.TestCase):
    def p(self, predicted, seen_at=T0, tracked=True):
        return Pending("7", 3, seen_at, predicted, tracked)

    def test_imminent_prediction_is_trusted(self):
        self.assertEqual(departure_time(self.p(T0 + 20), T0 + 30, True), T0 + 20)

    def test_never_later_than_when_the_stop_vanished(self):
        self.assertEqual(departure_time(self.p(T0 + 100), T0 + 30, True), T0 + 30)

    def test_untracked_trip_is_ignored(self):
        self.assertIsNone(departure_time(self.p(T0 + 20, tracked=False), T0 + 30, True))

    def test_early_bus_gets_midpoint(self):
        self.assertEqual(departure_time(self.p(T0 + 300), T0 + 30, True), T0 + 15)

    def test_early_bus_needs_trip_still_listed(self):
        self.assertIsNone(departure_time(self.p(T0 + 300), T0 + 30, False))

    def test_outage_gap_is_not_trusted(self):
        self.assertIsNone(departure_time(self.p(T0 + 600), T0 + 1500, True))

    def test_implausibly_early_is_ignored(self):
        self.assertIsNone(departure_time(self.p(T0 + 2000), T0 + 30, True))


class TrackerTest(unittest.TestCase):
    def setUp(self):
        self.conn = connect(":memory:")
        rows = [("bus", "A", "S1", 1, "12:00:00", "7"), ("bus", "A", "S2", 2, "12:05:00", "7"),
                ("bus", "A", "S3", 3, "12:10:00", "7")]
        self.conn.executemany("INSERT INTO scheduled_stops VALUES (?, ?, ?, ?, ?, ?)", rows)
        self.tracker = Tracker(self.conn, "bus")

    def departures(self):
        return self.conn.execute(
            "SELECT stop_id, observed, delay_s FROM departures ORDER BY stop_id"
        ).fetchall()

    def test_stop_that_drops_out_is_recorded_with_delay(self):
        self.tracker.process(snapshot(T0 + 380, [("A", [(2, "S2", T0 + 400), (3, "S3", T0 + 700)], True)]))
        self.tracker.process(snapshot(T0 + 410, [("A", [(3, "S3", T0 + 705)], True)]))
        self.assertEqual(self.departures(), [("S2", T0 + 400, 100)])  # 12:06:40 vs 12:05:00

    def test_untracked_trip_records_nothing(self):
        self.tracker.process(snapshot(T0 + 380, [("A", [(2, "S2", T0 + 400)], False)]))
        self.tracker.process(snapshot(T0 + 410, [("B", [(1, "X", T0 + 900)], True)]))
        self.assertEqual(self.departures(), [])

    def test_empty_snapshot_does_not_end_trips(self):
        self.tracker.process(snapshot(T0 + 380, [("A", [(2, "S2", T0 + 400)], True)]))
        self.tracker.process(snapshot(T0 + 410, []))
        self.assertEqual(self.departures(), [])
        self.assertEqual(len(self.tracker.pending), 1)

    def test_match_by_stop_id_when_sequence_is_off_by_one(self):
        # ION's realtime feed numbers stops one higher than its timetable.
        self.tracker.process(snapshot(T0 + 290, [("A", [(3, "S2", T0 + 300), (4, "S3", T0 + 600)], True)]))
        self.tracker.process(snapshot(T0 + 320, [("A", [(4, "S3", T0 + 600)], True)]))
        self.assertEqual(self.departures(), [("S2", T0 + 300, 0)])

    def test_stop_missing_from_timetable_has_no_delay(self):
        self.tracker.process(snapshot(T0, [("A", [(9, "ZZ", T0 + 10)], True)]))
        self.tracker.process(snapshot(T0 + 30, [("A", [(3, "S3", T0 + 600)], True)]))
        self.assertEqual(self.departures(), [("ZZ", T0 + 10, None)])

    def test_pending_survives_save_and_reload(self):
        self.tracker.process(snapshot(T0 + 380, [("A", [(2, "S2", T0 + 400)], True)]))
        self.tracker.save()
        later = Tracker(self.conn, "bus")
        later.process(snapshot(T0 + 410, [("A", [(3, "S3", T0 + 700)], True)]))
        self.assertEqual(self.departures(), [("S2", T0 + 400, 100)])


if __name__ == "__main__":
    unittest.main()
