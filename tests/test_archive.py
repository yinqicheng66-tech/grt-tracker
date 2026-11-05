import gzip
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

from collector.archive import (
    MAX_BACKOFF_S,
    Collector,
    Feed,
    backoff,
    snapshot_path,
)

FAST = Feed("fast", "https://example.test/fast", 30)
SLOW = Feed("slow", "https://example.test/slow", 300)
STATIC = "https://example.test/static.zip"
STATIC2 = "https://example.test/static2.zip"


class FakeServer:
    """Stands in for the network: returns queued bodies or raises queued errors."""

    def __init__(self):
        self.responses = {}
        self.calls = []

    def queue(self, url, *items):
        self.responses.setdefault(url, []).extend(items)

    def __call__(self, url):
        self.calls.append(url)
        item = self.responses[url].pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeTime:
    def __init__(self):
        self.mono = 1000.0
        self.wall = datetime(2026, 11, 5, 3, 4, 5, tzinfo=timezone.utc)

    def advance(self, seconds):
        self.mono += seconds
        self.wall += timedelta(seconds=seconds)


class CollectorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        self.server = FakeServer()
        self.time = FakeTime()
        self.collector = Collector(
            out=self.out,
            fetch=self.server,
            feeds=(FAST, SLOW),
            static_feeds=(("bus", STATIC),),
            clock=lambda: self.time.wall,
            monotonic=lambda: self.time.mono,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_saves_snapshot_gzipped_and_unchanged(self):
        self.server.queue(FAST.url, b"\x0a\x03abc")
        path = self.collector.poll(FAST)
        self.assertEqual(path, self.out / "fast" / "2026-11-05" / "030405.pb.gz")
        self.assertEqual(gzip.decompress(path.read_bytes()), b"\x0a\x03abc")
        self.assertEqual(self.collector.stats.saved, 1)

    def test_identical_snapshot_is_not_saved_twice(self):
        self.server.queue(FAST.url, b"same", b"same", b"new")
        self.assertIsNotNone(self.collector.poll(FAST))
        self.time.advance(30)
        self.assertIsNone(self.collector.poll(FAST))
        self.time.advance(30)
        self.assertIsNotNone(self.collector.poll(FAST))
        self.assertEqual(self.collector.stats.saved, 2)
        self.assertEqual(self.collector.stats.unchanged, 1)

    def test_same_bytes_produce_same_file(self):
        # gzip without a timestamp: equal input gives equal output, which keeps
        # the archive reproducible.
        self.server.queue(FAST.url, b"payload")
        first = self.collector.poll(FAST).read_bytes()
        self.assertEqual(first, gzip.compress(b"payload", compresslevel=6, mtime=0))

    def test_empty_response_is_an_error_not_a_file(self):
        self.server.queue(FAST.url, b"")
        self.assertIsNone(self.collector.poll(FAST))
        self.assertEqual(self.collector.stats.errors, 1)
        self.assertEqual(list(self.out.rglob("*.gz")), [])

    def test_failures_back_off_then_reset(self):
        err = urllib.error.URLError("down")
        self.server.queue(FAST.url, err, err, err, b"ok")
        state = self.collector.state["fast"]

        self.collector.poll(FAST)
        self.assertEqual(state.next_due, self.time.mono + 30)
        self.collector.poll(FAST)
        self.assertEqual(state.next_due, self.time.mono + 60)
        self.collector.poll(FAST)
        self.assertEqual(state.next_due, self.time.mono + 120)

        self.collector.poll(FAST)
        self.assertEqual(state.failures, 0)
        self.assertEqual(state.next_due, self.time.mono + 30)
        self.assertEqual(self.collector.stats.errors, 3)

    def test_timeout_is_handled(self):
        self.server.queue(FAST.url, TimeoutError("slow server"))
        self.assertIsNone(self.collector.poll(FAST))
        self.assertEqual(self.collector.stats.errors, 1)

    def test_poll_due_respects_each_feeds_interval(self):
        self.server.queue(FAST.url, b"1", b"2")
        self.server.queue(SLOW.url, b"a")
        self.collector.poll_due()  # both due at start
        self.time.advance(30)
        self.collector.poll_due()  # only the fast feed
        self.assertEqual(self.server.calls, [FAST.url, SLOW.url, FAST.url])
        self.assertAlmostEqual(self.collector.seconds_until_next(), 30)

    def test_static_saved_once_per_day(self):
        self.server.queue(STATIC, b"PK zip", b"PK zip 2")
        self.assertEqual(
            self.collector.save_static_if_missing(), [self.out / "static" / "bus" / "2026-11-05.zip"]
        )
        self.assertEqual(self.collector.save_static_if_missing(), [])
        self.time.advance(24 * 3600)
        self.assertEqual(
            self.collector.save_static_if_missing(), [self.out / "static" / "bus" / "2026-11-06.zip"]
        )
        self.assertEqual(self.server.calls, [STATIC, STATIC])

    def test_static_failure_is_retried_later(self):
        self.server.queue(STATIC, OSError("reset"), b"PK")
        self.assertEqual(self.collector.save_static_if_missing(), [])
        self.assertEqual(len(self.collector.save_static_if_missing()), 1)

    def test_static_rejects_non_zip(self):
        # An error page served with status 200 must not be stored as a timetable.
        self.server.queue(STATIC, b"<html>maintenance</html>")
        self.assertEqual(self.collector.save_static_if_missing(), [])
        self.assertEqual(list(self.out.rglob("*.zip")), [])

    def test_one_failing_timetable_does_not_block_the_other(self):
        self.collector.static_feeds = (("bus", STATIC), ("lrt", STATIC2))
        self.server.queue(STATIC, OSError("down"))
        self.server.queue(STATIC2, b"PK lrt")
        self.assertEqual(
            self.collector.save_static_if_missing(), [self.out / "static" / "lrt" / "2026-11-05.zip"]
        )

    def test_no_temporary_files_left_behind(self):
        self.server.queue(FAST.url, b"x")
        self.collector.poll(FAST)
        self.assertEqual(list(self.out.rglob("*.tmp")), [])


class FeedListTest(unittest.TestCase):
    def test_bus_and_lrt_feeds(self):
        from collector.archive import REALTIME_FEEDS, STATIC_FEEDS

        urls = {f.name: f.url for f in REALTIME_FEEDS}
        self.assertTrue(urls["bus_tripupdates"].endswith("/tripupdates/1"))
        self.assertTrue(urls["lrt_vehiclepositions"].endswith("/vehiclepositions/2"))
        self.assertTrue(urls["alerts"].endswith("/alerts"))
        self.assertEqual(len(REALTIME_FEEDS), 5)
        self.assertEqual([s for s, _ in STATIC_FEEDS], ["bus", "lrt"])


class HelperTest(unittest.TestCase):
    def test_path_uses_utc(self):
        toronto_evening = datetime(2026, 11, 5, 22, 30, tzinfo=timezone(timedelta(hours=-5)))
        self.assertEqual(
            snapshot_path(Path("raw"), "tripupdates", toronto_evening),
            Path("raw/tripupdates/2026-11-06/033000.pb.gz"),
        )

    def test_backoff_is_capped(self):
        self.assertEqual(backoff(30, 1), 30)
        self.assertEqual(backoff(30, 4), 240)
        self.assertEqual(backoff(30, 50), MAX_BACKOFF_S)


if __name__ == "__main__":
    unittest.main()
