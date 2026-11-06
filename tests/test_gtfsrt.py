import unittest

from pipeline.gtfsrt import DecodeError, iter_fields, parse_feed, read_varint
from tests import pb


class VarintTest(unittest.TestCase):
    def test_round_trip(self):
        for n in (0, 1, 127, 128, 300, 1793899384, 2**63 - 1):
            self.assertEqual(read_varint(pb.varint(n), 0), (n, len(pb.varint(n))))

    def test_truncated(self):
        with self.assertRaises(DecodeError):
            read_varint(b"\x80\x80", 0)

    def test_truncated_field(self):
        with self.assertRaises(DecodeError):
            list(iter_fields(pb.varint(1 << 3 | 2) + pb.varint(10) + b"short"))

    def test_unknown_fields_are_skipped(self):
        data = pb.int_field(99, 5) + pb.bytes_field(1, pb.int_field(3, 1700000000))
        self.assertEqual(parse_feed(data).timestamp, 1700000000)


class FeedTest(unittest.TestCase):
    def test_trip_update(self):
        data = pb.feed(
            1793899384,
            trip_updates=[
                pb.trip_update(
                    pb.trip("234", route_id="301", start_time="12:10:00"),
                    [pb.stop_update(5, "6112", time=1793899530), pb.stop_update(6, "6113", delay=-45)],
                    timestamp=1793899380,
                    vehicle_id="501",
                )
            ],
        )
        feed = parse_feed(data)
        self.assertEqual(feed.timestamp, 1793899384)
        tu = feed.trip_updates[0]
        self.assertEqual((tu.trip.trip_id, tu.trip.route_id, tu.trip.start_date), ("234", "301", "20261105"))
        self.assertEqual((tu.vehicle_id, tu.timestamp), ("501", 1793899380))
        first, second = tu.stop_time_updates
        self.assertEqual((first.stop_sequence, first.stop_id, first.departure.time), (5, "6112", 1793899530))
        self.assertEqual(second.departure.delay, -45)  # negative int32 decoded correctly

    def test_vehicle_position(self):
        data = pb.feed(1, vehicles=[pb.vehicle(pb.trip("4223387", "4"), "21101", 43.4495, -80.5235, 19, 1, 1793899359)])
        v = parse_feed(data).vehicles[0]
        self.assertEqual((v.vehicle_id, v.current_stop_sequence, v.current_status), ("21101", 19, 1))
        self.assertAlmostEqual(v.latitude, 43.4495, places=4)
        self.assertAlmostEqual(v.longitude, -80.5235, places=4)
        self.assertEqual(v.trip.route_id, "4")


if __name__ == "__main__":
    unittest.main()
