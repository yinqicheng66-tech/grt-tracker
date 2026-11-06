# GRT Tracker

[![CI](https://github.com/yinqicheng66-tech/grt-tracker/actions/workflows/ci.yml/badge.svg)](https://github.com/yinqicheng66-tech/grt-tracker/actions/workflows/ci.yml)

Measures how punctual Grand River Transit buses and the ION light rail in
Waterloo Region really are, by archiving the agency's live GTFS Realtime feeds
around the clock and comparing actual departures with the timetable.

> Work in progress. The archiver has run on a cloud server since 5 Nov 2026,
> and the pipeline turns its snapshots into per-stop delays. A dashboard comes next.

## First results

From 1.5 hours of data on the afternoon of 5 Nov 2026 (7,117 bus and 207 ION
departures; a first look, not yet a representative sample):

| | On time | Median delay | Worst routes |
| --- | --- | --- | --- |
| Buses | 90.5 % | 83 s late | 4 Glasgow-Margaret and 3 Ottawa South, about 34 % on time |
| ION light rail | 100 % | 18 s late | |

On time means leaving between 1 minute early and 5 minutes late.

## How it works

```
GRT GTFS-rt feeds ──every 30 s──▶ collector ──▶ raw archive (gzipped protobuf)
GRT timetables (GTFS zip) ─daily─▶ collector ──▶ static/<bus|lrt>/<date>.zip
                                                     │
                                      hourly ingest ─┴─▶ SQLite ──▶ report (dashboard next)
```

### 1. Collector

Stores every new snapshot exactly as received. Analysis runs on the archive
later, so a bug in the analysis never loses data, and the whole history can be
reprocessed after a fix.

- Fetches vehicle positions and trip updates every 30 seconds, separately for
  buses and the ION light rail, and service alerts every 5 minutes; both
  timetables once a day
- Skips snapshots identical to the previous one
- Writes each file atomically, so a crash or power loss never leaves half a file
- Backs off after failures (30 s, 60 s, 120 s, … up to 10 min) and recovers on its own
- Runs as a systemd service on an Oracle Cloud Always Free VM (1 GB RAM), using about 25 MB

### 2. Measuring departures

GRT's trip updates give, for each active trip, a predicted departure time at
every stop the vehicle has not reached yet; a stop drops out of the list once
the vehicle leaves it. The pipeline replays the snapshots in order and records
**the last prediction published before a stop disappeared** as the actual
departure, then compares it with the timetable.

Things that came up in the real data and how they are handled
(`pipeline/observe.py`):

- **ION numbers stops differently.** Its realtime feed counts stops from 1 and
  its timetable from 0, which first made every train look 2 minutes early.
  Departures are matched to the timetable by stop ID instead of sequence number.
- **Early buses.** A stop that vanishes before its predicted time means the bus
  left early; the time is taken as the midpoint between the two snapshots.
- **Collection outages.** After a gap in the archive, a stop's last prediction
  may be many minutes old, so such departures are discarded rather than guessed.
- **Untracked or cancelled trips.** Trips with no vehicle assigned only echo the
  timetable, and a trip that vanishes mid-route stopped being tracked; neither
  is counted.
- **Terminals.** A bus waiting at its first stop can drop off the feed before
  leaving, so first stops are left out of the statistics.
- **Daylight saving.** Timetable times are counted from "noon minus 12 hours",
  as the GTFS spec defines, so schedules stay correct on the days clocks change.

As a cross-check, departures timed from vehicle positions (when a bus's
current stop changes) agree to within a few seconds for buses (median 79 s against 81 s).

### 3. Standard library only

The protobuf decoder (`pipeline/gtfsrt.py`), the timetable loader and the
database (SQLite) use only Python's standard library, so the whole project runs
on a fresh server with nothing to install.

## Run it

Requires Python 3.9 or newer.

```bash
python3 -m collector --out data/raw                         # keep running
python3 -m pipeline.ingest --raw data/raw --db data/grt.db  # process new snapshots
python3 -m pipeline.report --db data/grt.db                 # on-time by route
```

Snapshots land in `data/raw/<feed>/<UTC date>/<UTC time>.pb.gz`. The ingest is
incremental: it remembers the last snapshot it processed and carries unfinished
trips over to the next run.

On the server, `deploy/` holds the systemd units: the collector runs
continuously and `grt-ingest.timer` processes new data every hour.

## Run tests

```bash
python3 -m unittest -v
```

## Data

Feeds and timetables from [GRT open data](https://www.grt.ca/about-grt/open-data/),
used under the Region of Waterloo open data licence.
