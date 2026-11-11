"""Process new archive files into the database.

    python -m pipeline.ingest --raw data/raw --db data/grt.db

Safe to run repeatedly (for example once an hour): it loads timetables it has
not seen and only the snapshots newer than the last run.
"""
import argparse
import gzip
import logging
import sqlite3
import zlib
from pathlib import Path
from typing import List, Optional

from pipeline.db import connect
from pipeline.gtfsrt import DecodeError, parse_feed
from pipeline.observe import Tracker, repair_day_offsets
from pipeline.timetable import load_timetable

log = logging.getLogger("ingest")

SYSTEMS = ("bus", "lrt")
COMMIT_EVERY = 200  # snapshots


def load_timetables(conn: sqlite3.Connection, raw: Path) -> int:
    loaded = 0
    for system in SYSTEMS:
        for path in sorted((raw / "static" / system).glob("*.zip")):
            if load_timetable(conn, system, str(path)):
                log.info("loaded %s timetable %s", system, path.name)
                loaded += 1
    return loaded


def new_snapshots(conn: sqlite3.Connection, raw: Path, feed: str) -> List[Path]:
    root = raw / feed
    row = conn.execute("SELECT last_file FROM progress WHERE feed = ?", (feed,)).fetchone()
    last: Optional[str] = row[0] if row else None
    # Paths are "<YYYY-MM-DD>/<HHMMSS>.pb.gz" in UTC, so text order is time order.
    files = sorted(root.glob("*/*.pb.gz"))
    return [f for f in files if last is None or str(f.relative_to(root)) > last]


def ingest_system(conn: sqlite3.Connection, raw: Path, system: str, limit: Optional[int] = None) -> Tracker:
    feed_name = f"{system}_tripupdates"
    tracker = Tracker(conn, system)
    files = new_snapshots(conn, raw, feed_name)
    if limit is not None:
        files = files[:limit]
    for i, path in enumerate(files, 1):
        try:
            tracker.process(parse_feed(gzip.decompress(path.read_bytes())))
        except (OSError, EOFError, zlib.error, DecodeError) as exc:
            log.warning("skipping %s: %s", path, exc)
        if i % COMMIT_EVERY == 0 or i == len(files):
            tracker.save()
            conn.execute(
                "INSERT OR REPLACE INTO progress VALUES (?, ?)",
                (feed_name, str(path.relative_to(raw / feed_name))),
            )
            conn.commit()
    log.info(
        "%s: %d snapshots, %d departures recorded, %d discarded, %d stops pending",
        system, len(files), tracker.recorded, tracker.discarded, len(tracker.pending),
    )
    return tracker


def main() -> None:
    parser = argparse.ArgumentParser(description="Load new GRT snapshots into the database.")
    parser.add_argument("--raw", default="data/raw", help="archive folder (default: data/raw)")
    parser.add_argument("--db", default="data/grt.db", help="SQLite database (default: data/grt.db)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    raw = Path(args.raw)
    conn = connect(args.db)
    load_timetables(conn, raw)
    repaired = repair_day_offsets(conn)
    if repaired:
        log.info("corrected %d departures recorded a service day off", repaired)
    for system in SYSTEMS:
        ingest_system(conn, raw, system)
    conn.close()


if __name__ == "__main__":
    main()
