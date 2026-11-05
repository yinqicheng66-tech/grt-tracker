# GRT Tracker

Measures how punctual Grand River Transit buses and the ION light rail in
Waterloo Region really are, by archiving the agency's live GTFS Realtime feeds
around the clock and comparing actual arrivals with the timetable.

> Work in progress. Step 1, the feed archiver, is running; delay analysis and
> the dashboard come next.

## How it works

```
GRT GTFS-rt feeds ──every 30 s──▶ collector ──▶ raw archive (gzipped protobuf)
GRT timetable (GTFS zip) ──daily──▶ collector ──▶ static/<date>.zip
                                                     │
                                         (next) parser ──▶ PostgreSQL ──▶ API + dashboard
```

The collector stores every new snapshot exactly as received. Analysis runs
on the archive later, so a bug in the analysis never loses data, and the
whole history can be reprocessed after a fix.

- Fetches vehicle positions and trip updates every 30 seconds and service
  alerts every 5 minutes; the timetable once a day
- Skips snapshots identical to the previous one
- Writes each file atomically, so a crash or power loss never leaves half a file
- Backs off after failures (30 s, 60 s, 120 s, … up to 10 min) and recovers on its own
- Standard library only: runs on a fresh server with no packages installed

## Run the collector

Requires Python 3.9 or newer.

```bash
python3 -m collector --out data/raw
```

Stop it with Control+C. Snapshots land in `data/raw/<feed>/<UTC date>/<UTC time>.pb.gz`.

## Run tests

```bash
python3 -m unittest -v
```

## Data

Feeds and timetable from the [Region of Waterloo open data portal](https://www.regionofwaterloo.ca/en/regional-government/open-data.aspx),
used under the Region of Waterloo open data licence.
