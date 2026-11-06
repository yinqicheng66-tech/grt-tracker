"""Load GTFS timetable zips and turn timetable times into real timestamps."""
import csv
import io
import sqlite3
import zipfile
from datetime import datetime
from typing import Dict, Iterator
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Toronto")


def _rows(z: zipfile.ZipFile, name: str) -> Iterator[Dict[str, str]]:
    if name not in z.namelist():
        return iter(())
    # utf-8-sig drops the byte-order mark some GTFS exports start with.
    text = io.TextIOWrapper(z.open(name), encoding="utf-8-sig", newline="")
    return ({k.strip(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(text))


def load_timetable(conn: sqlite3.Connection, system: str, path: str) -> bool:
    """Load one GTFS zip. Returns False if this file was loaded before."""
    if conn.execute("SELECT 1 FROM loaded_timetables WHERE path = ?", (path,)).fetchone():
        return False
    with zipfile.ZipFile(path) as z:
        conn.executemany(
            "INSERT OR REPLACE INTO routes VALUES (?, ?, ?, ?)",
            ((system, r["route_id"], r.get("route_short_name"), r.get("route_long_name"))
             for r in _rows(z, "routes.txt")),
        )
        conn.executemany(
            "INSERT OR REPLACE INTO stops VALUES (?, ?, ?, ?, ?)",
            ((system, r["stop_id"], r.get("stop_name"), _float(r.get("stop_lat")), _float(r.get("stop_lon")))
             for r in _rows(z, "stops.txt")),
        )
        route_of = {r["trip_id"]: r["route_id"] for r in _rows(z, "trips.txt")}
        conn.executemany(
            "INSERT OR REPLACE INTO scheduled_stops VALUES (?, ?, ?, ?, ?, ?)",
            ((system, r["trip_id"], r["stop_id"], int(r["stop_sequence"]),
              r["departure_time"] or r["arrival_time"], route_of.get(r["trip_id"], ""))
             for r in _rows(z, "stop_times.txt") if (r["departure_time"] or r["arrival_time"])),
        )
    conn.execute("INSERT INTO loaded_timetables VALUES (?)", (path,))
    conn.commit()
    return True


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def timetable_time(service_date: str, hms: str) -> int:
    """Unix time of a GTFS time on a service day.

    GTFS measures times from "noon minus 12 hours" in local time, which is
    midnight except on daylight-saving days, and allows hours past 24 for
    trips that run after midnight.
    """
    h, m, s = (int(x) for x in hms.split(":"))
    noon = datetime(int(service_date[:4]), int(service_date[4:6]), int(service_date[6:]), 12, tzinfo=TZ)
    # Work in absolute seconds: Python's arithmetic on aware datetimes uses
    # wall-clock time, which would be an hour off on daylight-saving days.
    return int(noon.timestamp()) - 12 * 3600 + h * 3600 + m * 60 + s
