"""Poll the GRT feeds and store every new snapshot, gzipped, exactly as received.

Raw snapshots are the source of truth: parsing and analysis run on them later,
so a bug in the analysis never loses data. Uses only the Python standard
library, so it runs on a fresh server with no packages installed.

Layout of the archive (dates and times in UTC, so daylight saving never makes
two files share a name):

    <out>/<feed>/<YYYY-MM-DD>/<HHMMSS>.pb.gz       realtime feeds
    <out>/static/<YYYY-MM-DD>.zip                  the timetable, once a day
"""
import gzip
import hashlib
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

log = logging.getLogger("collector")

BASE = "https://webapps.regionofwaterloo.ca/api/grt-routes/api"


@dataclass(frozen=True)
class Feed:
    name: str
    url: str
    every_s: int  # how often to fetch it


REALTIME_FEEDS = (
    Feed("vehiclepositions", BASE + "/vehiclepositions", 30),
    Feed("tripupdates", BASE + "/tripupdates", 30),
    Feed("alerts", BASE + "/alerts", 300),
)
STATIC_URL = "https://www.regionofwaterloo.ca/opendatadownloads/GRT_GTFS.zip"

USER_AGENT = "grt-tracker (student project; https://github.com/yinqicheng66-tech/grt-tracker)"
TIMEOUT_S = 20
MAX_BACKOFF_S = 600

# A fetcher takes a URL and returns the response body.
Fetcher = Callable[[str], bytes]


def http_get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        return response.read()


def snapshot_path(out: Path, feed: str, now: datetime) -> Path:
    utc = now.astimezone(timezone.utc)
    return out / feed / utc.strftime("%Y-%m-%d") / (utc.strftime("%H%M%S") + ".pb.gz")


def write_atomic(path: Path, data: bytes) -> None:
    """Write via a temporary file and rename, so a crash never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


@dataclass
class FeedState:
    next_due: float = 0.0  # monotonic time of the next fetch
    last_hash: Optional[str] = None
    failures: int = 0


@dataclass
class Stats:
    saved: int = 0
    unchanged: int = 0
    errors: int = 0
    bytes_saved: int = 0


@dataclass
class Collector:
    out: Path
    fetch: Fetcher = http_get
    feeds: Tuple[Feed, ...] = REALTIME_FEEDS
    static_url: Optional[str] = STATIC_URL
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    monotonic: Callable[[], float] = time.monotonic
    state: Dict[str, FeedState] = field(default_factory=dict)
    stats: Stats = field(default_factory=Stats)

    def __post_init__(self) -> None:
        for feed in self.feeds:
            self.state.setdefault(feed.name, FeedState())

    # ----- realtime feeds -----

    def poll(self, feed: Feed) -> Optional[Path]:
        """Fetch one feed once. Returns the file written, or None."""
        state = self.state[feed.name]
        try:
            body = self.fetch(feed.url)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            state.failures += 1
            self.stats.errors += 1
            delay = backoff(feed.every_s, state.failures)
            state.next_due = self.monotonic() + delay
            log.warning("%s: fetch failed (%s); retrying in %ds", feed.name, exc, delay)
            return None

        state.failures = 0
        state.next_due = self.monotonic() + feed.every_s
        if not body:
            self.stats.errors += 1
            log.warning("%s: empty response", feed.name)
            return None

        digest = hashlib.sha256(body).hexdigest()
        if digest == state.last_hash:
            self.stats.unchanged += 1  # the server has not published anything new
            return None

        path = snapshot_path(self.out, feed.name, self.clock())
        data = gzip.compress(body, compresslevel=6, mtime=0)
        write_atomic(path, data)
        state.last_hash = digest
        self.stats.saved += 1
        self.stats.bytes_saved += len(data)
        return path

    def poll_due(self) -> None:
        now = self.monotonic()
        for feed in self.feeds:
            if self.state[feed.name].next_due <= now:
                self.poll(feed)

    def seconds_until_next(self) -> float:
        soonest = min(s.next_due for s in self.state.values())
        return max(0.0, soonest - self.monotonic())

    # ----- static timetable -----

    def save_static_if_missing(self) -> Optional[Path]:
        """Download today's timetable unless it is already saved."""
        if not self.static_url:
            return None
        day = self.clock().astimezone(timezone.utc).strftime("%Y-%m-%d")
        path = self.out / "static" / (day + ".zip")
        if path.exists():
            return None
        try:
            body = self.fetch(self.static_url)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            log.warning("static timetable: fetch failed (%s); will retry", exc)
            return None
        write_atomic(path, body)
        log.info("static timetable saved: %s (%d KB)", path, len(body) // 1024)
        return path


def backoff(every_s: int, failures: int) -> int:
    """Wait longer after each failure in a row, up to MAX_BACKOFF_S."""
    return min(MAX_BACKOFF_S, every_s * 2 ** min(failures - 1, 10))
