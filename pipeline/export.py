"""Export dashboard statistics as one JSON file.

    python -m pipeline.export --db data/grt.db --out site/data.json

The dashboard (web/) is a static page that reads this file, so it can be
hosted anywhere, for example GitHub Pages, with no server of its own.
"""
import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from pipeline.report import EARLY_S, LATE_S, percentile
from pipeline.timetable import TZ

ROUTE_MIN = 30  # departures a route needs before it is ranked
STOP_MIN = 30  # departures a stop needs before it appears on the map
STOP_LIMIT = 300  # stops sent to the map

ROWS = """
WITH first_stop AS (
    SELECT s.system, s.trip_id, s.stop_id
    FROM scheduled_stops s
    JOIN (SELECT system, trip_id, MIN(stop_sequence) AS seq
          FROM scheduled_stops GROUP BY system, trip_id) f
      ON s.system = f.system AND s.trip_id = f.trip_id AND s.stop_sequence = f.seq
)
SELECT d.system, d.route_id, d.stop_id, d.scheduled, d.delay_s
FROM departures d
LEFT JOIN first_stop fs
  ON fs.system = d.system AND fs.trip_id = d.trip_id AND fs.stop_id = d.stop_id
WHERE fs.trip_id IS NULL AND d.delay_s IS NOT NULL
"""


def stats(delays: List[int]) -> Dict[str, float]:
    d = sorted(delays)
    n = len(d)
    return {
        "departures": n,
        "on_time": round(sum(EARLY_S <= x <= LATE_S for x in d) / n, 4),
        "early": round(sum(x < EARLY_S for x in d) / n, 4),
        "late": round(sum(x > LATE_S for x in d) / n, 4),
        "median": percentile(d, 0.5),
        "p90": percentile(d, 0.9),
    }


def build(
    conn: sqlite3.Connection,
    now: Optional[datetime] = None,
    route_min: int = ROUTE_MIN,
    stop_min: int = STOP_MIN,
) -> Dict:
    by_system = defaultdict(list)
    by_route = defaultdict(list)
    by_hour = defaultdict(list)
    by_day = defaultdict(list)
    by_stop = defaultdict(list)
    first = last = None
    for system, route_id, stop_id, scheduled, delay in conn.execute(ROWS):
        local = datetime.fromtimestamp(scheduled, TZ)
        by_system[system].append(delay)
        by_route[(system, route_id)].append(delay)
        by_hour[(system, local.hour)].append(delay)
        by_day[(system, local.strftime("%Y-%m-%d"))].append(delay)
        by_stop[(system, stop_id)].append(delay)
        first = scheduled if first is None else min(first, scheduled)
        last = scheduled if last is None else max(last, scheduled)

    routes = {(s, r): (short or r, long or "") for s, r, short, long in conn.execute("SELECT * FROM routes")}
    stops = {(s, i): (name, lat, lon) for s, i, name, lat, lon in conn.execute("SELECT * FROM stops")}

    stop_rows = []
    for (system, stop_id), delays in by_stop.items():
        name, lat, lon = stops.get((system, stop_id), (stop_id, None, None))
        if len(delays) >= stop_min and lat is not None:
            stop_rows.append({"system": system, "stop_id": stop_id, "name": name, "lat": lat, "lon": lon, **stats(delays)})
    stop_rows.sort(key=lambda s: -s["median"])

    def iso(ts):
        return datetime.fromtimestamp(ts, TZ).isoformat(timespec="minutes") if ts else None

    return {
        "generated_at": (now or datetime.now(timezone.utc)).astimezone(TZ).isoformat(timespec="minutes"),
        "first_departure": iso(first),
        "last_departure": iso(last),
        "on_time_window": {"early_s": EARLY_S, "late_s": LATE_S},
        "systems": {s: stats(v) for s, v in sorted(by_system.items())},
        "routes": sorted(
            (
                {"system": s, "route": routes.get((s, r), (r, ""))[0], "name": routes.get((s, r), (r, ""))[1], **stats(v)}
                for (s, r), v in by_route.items()
                if len(v) >= route_min
            ),
            key=lambda x: x["on_time"],
        ),
        "hourly": {
            s: [{"hour": h, **stats(by_hour[(s, h)])} for h in range(24) if by_hour.get((s, h))]
            for s in sorted(by_system)
        },
        "daily": {
            s: [{"date": d, **stats(v)} for (sy, d), v in sorted(by_day.items()) if sy == s]
            for s in sorted(by_system)
        },
        "stops": stop_rows[:STOP_LIMIT],
    }


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Export dashboard data as JSON.")
    parser.add_argument("--db", default="data/grt.db")
    parser.add_argument("--out", default="site/data.json")
    parser.add_argument("--route-min", type=int, default=ROUTE_MIN, help="departures needed to rank a route")
    parser.add_argument("--stop-min", type=int, default=STOP_MIN, help="departures needed to map a stop")
    args = parser.parse_args(argv)
    data = build(sqlite3.connect(args.db), route_min=args.route_min, stop_min=args.stop_min)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    tmp.replace(out)
    print(f"wrote {out} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
