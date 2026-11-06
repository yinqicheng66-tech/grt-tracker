"""SQLite storage for timetables and observed departures."""
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS routes (
    system      TEXT NOT NULL,           -- 'bus' or 'lrt'
    route_id    TEXT NOT NULL,
    short_name  TEXT,
    long_name   TEXT,
    PRIMARY KEY (system, route_id)
);

CREATE TABLE IF NOT EXISTS stops (
    system   TEXT NOT NULL,
    stop_id  TEXT NOT NULL,
    name     TEXT,
    lat      REAL,
    lon      REAL,
    PRIMARY KEY (system, stop_id)
);

-- Scheduled departure of each trip from each stop, from the GTFS timetable.
CREATE TABLE IF NOT EXISTS scheduled_stops (
    system         TEXT NOT NULL,
    trip_id        TEXT NOT NULL,
    stop_id        TEXT NOT NULL,
    stop_sequence  INTEGER NOT NULL,
    departure      TEXT NOT NULL,        -- 'HH:MM:SS', may be past 24:00:00
    route_id       TEXT NOT NULL,
    PRIMARY KEY (system, trip_id, stop_id, stop_sequence)
);

-- Timetable zips already loaded, so each is read once.
CREATE TABLE IF NOT EXISTS loaded_timetables (
    path TEXT PRIMARY KEY
);

-- One row per trip per stop that a vehicle was seen to pass.
CREATE TABLE IF NOT EXISTS departures (
    system        TEXT NOT NULL,
    service_date  TEXT NOT NULL,         -- 'YYYYMMDD', the timetable day
    trip_id       TEXT NOT NULL,
    stop_id       TEXT NOT NULL,
    route_id      TEXT,
    stop_sequence INTEGER,
    scheduled     INTEGER,               -- Unix time, NULL if not in the timetable
    observed      INTEGER NOT NULL,      -- Unix time
    delay_s       INTEGER,               -- observed - scheduled
    PRIMARY KEY (system, service_date, trip_id, stop_id)
);
CREATE INDEX IF NOT EXISTS departures_route ON departures (system, route_id, observed);

-- Predictions for stops not yet passed, carried between runs.
CREATE TABLE IF NOT EXISTS pending (
    system        TEXT NOT NULL,
    service_date  TEXT NOT NULL,
    trip_id       TEXT NOT NULL,
    stop_id       TEXT NOT NULL,
    route_id      TEXT,
    stop_sequence INTEGER,
    seen_at       INTEGER NOT NULL,      -- feed time of the last snapshot listing it
    predicted     INTEGER NOT NULL,      -- predicted departure at that time
    tracked       INTEGER NOT NULL,      -- 1 if a vehicle was assigned to the trip
    PRIMARY KEY (system, service_date, trip_id, stop_id)
);

-- Last snapshot processed for each feed, so runs pick up where they left off.
CREATE TABLE IF NOT EXISTS progress (
    feed       TEXT PRIMARY KEY,
    last_file  TEXT NOT NULL
);
"""


def connect(path: str) -> sqlite3.Connection:
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn
