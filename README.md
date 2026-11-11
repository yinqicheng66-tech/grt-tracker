# GRT Tracker

[![CI](https://github.com/yinqicheng66-tech/grt-tracker/actions/workflows/ci.yml/badge.svg)](https://github.com/yinqicheng66-tech/grt-tracker/actions/workflows/ci.yml)

Measures how punctual Grand River Transit buses and the ION light rail in
Waterloo Region really are, by archiving the agency's live GTFS Realtime feeds
around the clock and comparing actual departures with the timetable.

**Dashboard: https://yinqicheng66-tech.github.io/grt-tracker/** (updated hourly)

## Results

From the first week of data, 5–11 Nov 2026 (about 633,000 bus and 16,000 ION
departures). The dashboard keeps updating as data accumulates.

| | On time | Median delay | 90 % of departures are no later than |
| --- | --- | --- | --- |
| Buses | 89.7 % | 79 s late | 5 min 4 s late |
| ION light rail | 98.6 % | 8 s late | 68 s late |

On time means leaving between 1 minute early and 5 minutes late.

- **The afternoon rush is the problem.** Buses leave on time 98 % of the time
  at 7 a.m. but only 80 % between 4 and 7 p.m., when the median delay grows to
  about 2 minutes.
- **Day to day,** on-time rates ranged from 85 % (5 Nov, which only covers
  the afternoon and evening) to 93 % (Sun 8 Nov and Mon 9 Nov).
- **Least punctual routes:** 3 Ottawa South (72 %), 20 Victoria-Frederick
  (74 %), 7 King (76 %) and 4 Glasgow-Margaret (76 %).
- **Buses almost never leave early** (0.04 % of departures).

## How it works

```
GRT GTFS-rt feeds ──every 30 s──▶ collector ──▶ raw archive (gzipped protobuf)
GRT timetables (GTFS zip) ─daily─▶ collector ──▶ static/<bus|lrt>/<date>.zip
                                                     │
                                      hourly ingest ─┴─▶ SQLite ──▶ data.json ──▶ dashboard (GitHub Pages)
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
- **Trips after midnight.** The timetable lists a 12:30 a.m. trip as
  "24:30" on the previous day, but the realtime feed reports it under the next
  date, which first put the late-night route a whole day early. Each departure
  is now matched to whichever service day puts its schedule closest to when it
  actually ran.
- **Daylight saving.** Timetable times are counted from "noon minus 12 hours",
  as the GTFS spec defines, so schedules stay correct on the days clocks change.

As a cross-check on the first day of data, departures timed from vehicle
positions (when a bus's current stop changes) agreed to within a few seconds
for buses (median 79 s against 81 s).

### 3. Dashboard

`pipeline/export.py` turns the database into one small JSON file, and `web/` is
a static page (plain JavaScript and SVG charts, Leaflet for the map) that reads
it. After each hourly ingest, `deploy/publish.sh` pushes the page and the
fresh data to the `gh-pages` branch, which GitHub Pages serves. The branch
keeps a single commit, so the repository does not grow every hour.

### 4. Standard library only

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
continuously and `grt-ingest.timer` processes new data and republishes the
dashboard every hour.

Publishing needs a deploy key once: generate one on the server
(`ssh-keygen -t ed25519 -f ~/.ssh/grt-deploy -N ""`), add the public key under the
repository's Settings → Deploy keys with write access, point `github.com` at it
in `~/.ssh/config`, run `deploy/publish.sh` once, and set Settings → Pages to
deploy from the `gh-pages` branch.

To preview the page locally:

```bash
python3 -m pipeline.export --db data/grt.db --out web/data.json
cd web && python3 -m http.server 8000   # then open http://localhost:8000
```

## Run tests

```bash
python3 -m unittest -v
```

## Data

Feeds and timetables from [GRT open data](https://www.grt.ca/about-grt/open-data/),
used under the Region of Waterloo open data licence.
