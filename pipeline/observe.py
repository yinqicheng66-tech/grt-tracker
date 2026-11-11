"""Turn a stream of trip-update snapshots into observed departure times.

GRT's trip updates list, for every active trip, a predicted departure time for
each stop the vehicle has not reached yet. Once the vehicle leaves a stop, the
stop drops out of the list. So the last prediction published before a stop
disappears is our measurement of when the vehicle actually left it.

A stop can also vanish before its predicted time when the bus runs early.
If that happens between two consecutive snapshots, the bus left somewhere in
between, and the midpoint is recorded.

Guards keep bad data out:
- the trip must have had a vehicle assigned (otherwise the "prediction" is just
  the timetable);
- an early departure is only trusted if the trip was still being tracked, the
  snapshots were close together (no collection outage in between), and the
  prediction was off by less than MAX_AHEAD_S. Anything else, such as a whole
  trip vanishing mid-route or a stop missing after an outage, is discarded.
"""
import sqlite3
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from pipeline.gtfsrt import Feed
from pipeline.timetable import nearest_timetable_time

GRACE_S = 120  # a final prediction this close to its snapshot is a departure
MAX_AHEAD_S = 600  # trust a stop vanishing at most this long before its prediction
MAX_GAP_S = 90  # snapshots further apart than this mean a collection outage

SCHEDULED, SKIPPED = 0, 1  # GTFS-rt schedule_relationship values

Key = Tuple[str, str, str]  # (service date, trip id, stop id)


@dataclass
class Pending:
    route_id: Optional[str]
    stop_sequence: Optional[int]
    seen_at: int
    predicted: int
    tracked: bool


def departure_time(p: Pending, gone_at: int, trip_listed: bool) -> Optional[int]:
    """When the vehicle left a stop that first went missing at gone_at, or None if unknown."""
    if not p.tracked:
        return None
    if p.predicted <= p.seen_at + GRACE_S:
        # The departure was imminent when last listed: trust the prediction,
        # unless the stop was already gone before the predicted time.
        return min(p.predicted, gone_at)
    gap = gone_at - p.seen_at
    if trip_listed and gap <= MAX_GAP_S and p.predicted - gone_at <= MAX_AHEAD_S:
        return p.seen_at + gap // 2  # left early, between two snapshots
    return None


class Tracker:
    def __init__(self, conn: sqlite3.Connection, system: str):
        self.conn = conn
        self.system = system
        self.pending: Dict[Key, Pending] = {}
        self.recorded = 0
        self.discarded = 0
        for row in conn.execute(
            "SELECT service_date, trip_id, stop_id, route_id, stop_sequence, seen_at, predicted, tracked"
            " FROM pending WHERE system = ?",
            (system,),
        ):
            self.pending[row[:3]] = Pending(row[3], row[4], row[5], row[6], bool(row[7]))

    def process(self, feed: Feed) -> None:
        if feed.timestamp is None or not feed.trip_updates:
            return  # an empty or broken snapshot says nothing about what departed
        present = set()
        trips_present = set()
        for update in feed.trip_updates:
            trip = update.trip
            if trip.schedule_relationship != SCHEDULED or not trip.trip_id or not trip.start_date:
                continue
            trips_present.add((trip.start_date, trip.trip_id))
            tracked = update.timestamp is not None or update.vehicle_id is not None
            for stu in update.stop_time_updates:
                if stu.schedule_relationship == SKIPPED or not stu.stop_id:
                    continue
                event = stu.departure if stu.departure and stu.departure.time else stu.arrival
                if not event or not event.time:
                    continue
                key = (trip.start_date, trip.trip_id, stu.stop_id)
                present.add(key)
                before = self.pending.get(key)
                self.pending[key] = Pending(
                    route_id=trip.route_id,
                    stop_sequence=stu.stop_sequence,
                    seen_at=feed.timestamp,
                    predicted=event.time,
                    tracked=tracked or (before.tracked if before else False),
                )
        for key in [k for k in self.pending if k not in present]:
            trip_listed = key[:2] in trips_present
            self._finish(key, self.pending.pop(key), feed.timestamp, trip_listed)

    def _finish(self, key: Key, p: Pending, gone_at: int, trip_listed: bool) -> None:
        observed = departure_time(p, gone_at, trip_listed)
        if observed is None:
            self.discarded += 1
            return
        service_date, trip_id, stop_id = key
        scheduled = self._scheduled(service_date, trip_id, stop_id, p.stop_sequence, observed)
        self.conn.execute(
            "INSERT OR REPLACE INTO departures VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                self.system, service_date, trip_id, stop_id, p.route_id, p.stop_sequence,
                scheduled, observed, None if scheduled is None else observed - scheduled,
            ),
        )
        self.recorded += 1

    def _scheduled(
        self, service_date: str, trip_id: str, stop_id: str, seq: Optional[int], observed: int
    ) -> Optional[int]:
        # Match on stop id, not stop sequence: the ION realtime feed numbers
        # stops from 1 while its timetable numbers them from 0. If a trip
        # visits the same stop twice, take the visit with the nearest number.
        row = self.conn.execute(
            "SELECT departure FROM scheduled_stops WHERE system = ? AND trip_id = ? AND stop_id = ?"
            " ORDER BY ABS(stop_sequence - ?) LIMIT 1",
            (self.system, trip_id, stop_id, seq or 0),
        ).fetchone()
        return nearest_timetable_time(service_date, row[0], observed) if row else None

    def save(self) -> None:
        """Store unfinished predictions so the next run can continue them."""
        self.conn.execute("DELETE FROM pending WHERE system = ?", (self.system,))
        self.conn.executemany(
            "INSERT INTO pending VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                (self.system, *key, p.route_id, p.stop_sequence, p.seen_at, p.predicted, int(p.tracked))
                for key, p in self.pending.items()
            ),
        )


def repair_day_offsets(conn: sqlite3.Connection) -> int:
    """Fix departures stored a whole service day off, from before nearest_timetable_time.

    Returns how many rows were corrected.
    """
    rows = conn.execute(
        "SELECT system, service_date, trip_id, stop_id, stop_sequence, observed"
        " FROM departures WHERE ABS(delay_s) > 43200"
    ).fetchall()
    for system, service_date, trip_id, stop_id, seq, observed in rows:
        hms = conn.execute(
            "SELECT departure FROM scheduled_stops WHERE system = ? AND trip_id = ? AND stop_id = ?"
            " ORDER BY ABS(stop_sequence - ?) LIMIT 1",
            (system, trip_id, stop_id, seq or 0),
        ).fetchone()[0]
        scheduled = nearest_timetable_time(service_date, hms, observed)
        conn.execute(
            "UPDATE departures SET scheduled = ?, delay_s = ?"
            " WHERE system = ? AND service_date = ? AND trip_id = ? AND stop_id = ?",
            (scheduled, observed - scheduled, system, service_date, trip_id, stop_id),
        )
    conn.commit()
    return len(rows)
