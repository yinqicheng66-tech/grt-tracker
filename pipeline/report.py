"""Print on-time performance by route.

    python -m pipeline.report --db data/grt.db [--since 2026-11-05]

On time means leaving between 1 minute early and 5 minutes late, a common
North American transit standard. The first stop of each trip is left out:
a bus waiting at its terminal can drop off the feed before it actually leaves.
"""
import argparse
import sqlite3
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Sequence

from pipeline.timetable import TZ

EARLY_S, LATE_S = -60, 300

QUERY = """
WITH first_stop AS (
    SELECT s.system, s.trip_id, s.stop_id
    FROM scheduled_stops s
    JOIN (SELECT system, trip_id, MIN(stop_sequence) AS seq
          FROM scheduled_stops GROUP BY system, trip_id) f
      ON s.system = f.system AND s.trip_id = f.trip_id AND s.stop_sequence = f.seq
)
SELECT d.system, d.route_id, d.delay_s
FROM departures d
LEFT JOIN first_stop fs
  ON fs.system = d.system AND fs.trip_id = d.trip_id AND fs.stop_id = d.stop_id
WHERE fs.trip_id IS NULL AND d.delay_s IS NOT NULL AND d.observed >= ?
"""


def percentile(sorted_values: Sequence[int], p: float) -> int:
    return sorted_values[round(p * (len(sorted_values) - 1))]


def summarize(delays: List[int]) -> Dict[str, float]:
    d = sorted(delays)
    n = len(d)
    return {
        "departures": n,
        "on_time": sum(EARLY_S <= x <= LATE_S for x in d) / n,
        "early": sum(x < EARLY_S for x in d) / n,
        "late": sum(x > LATE_S for x in d) / n,
        "median": percentile(d, 0.5),
        "p90": percentile(d, 0.9),
    }


def route_stats(conn: sqlite3.Connection, since: int = 0, min_departures: int = 1):
    by_route: Dict[tuple, List[int]] = defaultdict(list)
    by_system: Dict[str, List[int]] = defaultdict(list)
    for system, route_id, delay in conn.execute(QUERY, (since,)):
        by_route[(system, route_id)].append(delay)
        by_system[system].append(delay)
    names = {(s, r): (short or r, long or "") for s, r, short, long in conn.execute("SELECT * FROM routes")}
    routes = [
        {"system": s, "route": names.get((s, r), (r, ""))[0], "name": names.get((s, r), (r, ""))[1], **summarize(v)}
        for (s, r), v in by_route.items()
        if len(v) >= min_departures
    ]
    routes.sort(key=lambda x: x["on_time"])
    systems = {s: summarize(v) for s, v in by_system.items()}
    return systems, routes


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="On-time performance by route.")
    parser.add_argument("--db", default="data/grt.db")
    parser.add_argument("--since", help="only departures on or after this local date, YYYY-MM-DD")
    parser.add_argument("--min", type=int, default=20, help="hide routes with fewer departures")
    args = parser.parse_args(argv)

    since = 0
    if args.since:
        since = int(datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=TZ).timestamp())
    conn = sqlite3.connect(args.db)
    systems, routes = route_stats(conn, since, args.min)
    if not systems:
        print("No departures yet.")
        return

    print("On time = 1 min early to 5 min late. First stops excluded.\n")
    for system, s in sorted(systems.items()):
        print(
            f"{system.upper():4} {s['departures']:>7,} departures  on time {s['on_time']:6.1%}"
            f"  early {s['early']:5.1%}  late {s['late']:5.1%}  median {s['median']:+5d} s"
        )
    print(f"\n{'Route':<6}{'Name':<30}{'Deps':>7}{'On time':>9}{'Late':>7}{'Median':>8}{'90th %':>8}")
    for r in routes:
        print(
            f"{r['route']:<6}{r['name'][:29]:<30}{r['departures']:>7,}{r['on_time']:>9.1%}"
            f"{r['late']:>7.1%}{r['median']:>+7d}s{r['p90']:>+7d}s"
        )


if __name__ == "__main__":
    main()
