"""Run the collector: python -m collector --out data/raw"""
import argparse
import logging
import signal
import time
from pathlib import Path

from collector.archive import Collector

STATIC_CHECK_S = 3600  # look for a new day's timetable once an hour
STATS_EVERY_S = 600


def main() -> None:
    parser = argparse.ArgumentParser(description="Archive the GRT realtime feeds.")
    parser.add_argument("--out", default="data/raw", help="archive folder (default: data/raw)")
    parser.add_argument("--no-static", action="store_true", help="skip the daily timetable download")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("collector")

    collector = Collector(out=Path(args.out))
    if args.no_static:
        collector.static_url = None

    stopping = False

    def stop(signum, _frame):
        nonlocal stopping
        stopping = True
        log.info("got signal %d, stopping after this round", signum)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    log.info("archiving GRT feeds into %s", Path(args.out).resolve())
    next_static = 0.0
    next_stats = time.monotonic() + STATS_EVERY_S
    while not stopping:
        now = time.monotonic()
        if now >= next_static:
            collector.save_static_if_missing()
            next_static = now + STATIC_CHECK_S
        collector.poll_due()
        if now >= next_stats:
            s = collector.stats
            log.info(
                "so far: %d snapshots saved (%.1f MB), %d unchanged, %d errors",
                s.saved, s.bytes_saved / 1e6, s.unchanged, s.errors,
            )
            next_stats = now + STATS_EVERY_S
        # Sleep in short steps so Control+C stops the program quickly.
        wait = min(collector.seconds_until_next(), 1.0)
        if wait > 0:
            time.sleep(wait)
    log.info("stopped")


if __name__ == "__main__":
    main()
