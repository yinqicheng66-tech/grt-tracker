import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from pipeline import export, ingest
from pipeline.db import connect
from tests.test_pipeline import write_archive


class ExportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        raw = Path(self.tmp.name) / "raw"
        write_archive(raw)
        self.db = str(Path(self.tmp.name) / "grt.db")
        conn = connect(self.db)
        ingest.load_timetables(conn, raw)
        ingest.ingest_system(conn, raw, "bus")
        conn.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_build(self):
        data = export.build(connect(self.db), now=datetime(2026, 11, 5, 20, tzinfo=timezone.utc), route_min=1, stop_min=1)
        self.assertEqual(data["generated_at"], "2026-11-05T15:00-05:00")
        self.assertEqual(data["systems"]["bus"]["departures"], 2)  # first stop excluded
        self.assertEqual(data["systems"]["bus"]["median"], 80)
        self.assertEqual(data["routes"][0]["name"], "King")
        self.assertEqual([h["hour"] for h in data["hourly"]["bus"]], [12])  # local hour of the schedule
        self.assertEqual(data["daily"]["bus"][0]["date"], "2026-11-05")
        self.assertEqual([s["name"] for s in data["stops"]], ["End", "Middle"])  # longest delay first

    def test_thresholds_hide_thin_data(self):
        data = export.build(connect(self.db))
        self.assertEqual(data["routes"], [])
        self.assertEqual(data["stops"], [])

    def test_cli_writes_json(self):
        out = Path(self.tmp.name) / "site" / "data.json"
        export.main(["--db", self.db, "--out", str(out)])
        self.assertIn("systems", json.loads(out.read_text()))


if __name__ == "__main__":
    unittest.main()
