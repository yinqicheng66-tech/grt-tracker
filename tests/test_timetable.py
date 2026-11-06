import unittest
from datetime import datetime

from pipeline.timetable import TZ, timetable_time


def local(*args):
    return int(datetime(*args, tzinfo=TZ).timestamp())


class TimetableTimeTest(unittest.TestCase):
    def test_ordinary_day(self):
        self.assertEqual(timetable_time("20261105", "13:15:00"), local(2026, 11, 5, 13, 15))

    def test_after_midnight(self):
        # 25:10:00 on the 5th is 1:10 am on the 6th.
        self.assertEqual(timetable_time("20261105", "25:10:00"), local(2026, 11, 6, 1, 10))

    def test_daylight_saving_ends(self):
        # 1 Nov 2026: clocks go back at 2 am. GTFS counts from noon minus 12 h,
        # so afternoon times still match the wall clock...
        self.assertEqual(timetable_time("20261101", "15:00:00"), local(2026, 11, 1, 15, 0))
        # ...and the day starts at 01:00 EDT, not at midnight.
        self.assertEqual(timetable_time("20261101", "00:00:00"), local(2026, 11, 1, 1, 0))

    def test_daylight_saving_starts(self):
        # 8 Mar 2026: clocks go forward at 2 am.
        self.assertEqual(timetable_time("20260308", "15:00:00"), local(2026, 3, 8, 15, 0))


if __name__ == "__main__":
    unittest.main()
