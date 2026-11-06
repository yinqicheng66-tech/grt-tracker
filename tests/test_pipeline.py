"""End to end: a fake archive on disk through ingest and report."""
import gzip
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from pipeline import ingest
from pipeline.db import connect
from pipeline.report import route_stats
from pipeline.timetable import timetable_time
from tests import pb

DATE = "20261105"
T0 = timetable_time(DATE, "12:00:00")

TIMETABLE = {
    "routes.txt": "route_id,route_short_name,route_long_name\n7,7,King\n",
    "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\nS1,Terminal,43.4,-80.5\nS2,Middle,43.4,-80.5\nS3,End,43.4,-80.5\n",
    "trips.txt": "route_id,service_id,trip_id\n7,WK,A\n",
    "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
    "A,12:00:00,12:00:00,S1,1\nA,12:05:00,12:05:00,S2,2\nA,12:10:00,12:10:00,S3,3\n",
}

# (seconds after 12:00, remaining stops with predicted seconds after 12:00)
SNAPSHOTS = [
    (0, [("S1", 10), ("S2", 320), ("S3", 640)]),
    (30, [("S2", 330), ("S3", 650)]),   # S1 left at 12:00:10 (first stop)
    (300, [("S2", 340), ("S3", 660)]),
    (330, [("S2", 345), ("S3", 665)]),
    (360, [("S3", 670)]),               # S2 left at 12:05:45: 45 s late
    (650, [("S3", 680)]),
    (690, []),                          # trip finished: S3 left at 12:11:20, 80 s late
]


def write_archive(raw: Path) -> None:
    static = raw / "static" / "bus"
    static.mkdir(parents=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, body in TIMETABLE.items():
            z.writestr(name, body)
    (static / "2026-11-05.zip").write_bytes(buf.getvalue())

    day = raw / "bus_tripupdates" / "2026-11-05"
    day.mkdir(parents=True)
    other = pb.trip_update(pb.trip("OTHER", start_date=DATE), [pb.stop_update(1, "Q", time=T0 + 5000)])
    for offset, stops in SNAPSHOTS:
        updates = [other]
        if stops:
            stop_bytes = [pb.stop_update(i, s, time=T0 + t) for i, (s, t) in enumerate(stops, 1)]
            updates.append(pb.trip_update(pb.trip("A", start_date=DATE), stop_bytes, timestamp=T0 + offset))
        name = f"{17 + offset // 3600:02d}{offset // 60 % 60:02d}{offset % 60:02d}.pb.gz"
        (day / name).write_bytes(gzip.compress(pb.feed(T0 + offset, trip_updates=updates)))


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raw = Path(self.tmp.name) / "raw"
        write_archive(self.raw)

    def tearDown(self):
        self.tmp.cleanup()

    def run_ingest(self, db, limit=None):
        conn = connect(db)
        ingest.load_timetables(conn, self.raw)
        ingest.ingest_system(conn, self.raw, "bus", limit=limit)
        return conn

    def delays(self, conn):
        return conn.execute("SELECT stop_id, delay_s FROM departures ORDER BY stop_id").fetchall()

    def test_end_to_end(self):
        conn = self.run_ingest(":memory:")
        self.assertEqual(self.delays(conn), [("S1", 10), ("S2", 45), ("S3", 80)])
        systems, routes = route_stats(conn)
        # The first stop is left out of the statistics.
        self.assertEqual(systems["bus"]["departures"], 2)
        self.assertEqual(routes[0]["route"], "7")
        self.assertEqual(routes[0]["name"], "King")
        self.assertEqual(routes[0]["on_time"], 1.0)

    def test_running_in_pieces_gives_the_same_result(self):
        db = str(Path(self.tmp.name) / "grt.db")
        for _ in range(4):
            self.run_ingest(db, limit=2).close()
        conn = connect(db)
        self.assertEqual(self.delays(conn), [("S1", 10), ("S2", 45), ("S3", 80)])

    def test_rerun_does_nothing_new(self):
        db = str(Path(self.tmp.name) / "grt.db")
        self.run_ingest(db).close()
        conn = self.run_ingest(db)
        self.assertEqual(len(self.delays(conn)), 3)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM loaded_timetables").fetchone()[0], 1)

    def test_corrupt_snapshot_is_skipped(self):
        (self.raw / "bus_tripupdates" / "2026-11-05" / "170005.pb.gz").write_bytes(b"not gzip")
        conn = self.run_ingest(":memory:")
        self.assertEqual(self.delays(conn), [("S1", 10), ("S2", 45), ("S3", 80)])


if __name__ == "__main__":
    unittest.main()
