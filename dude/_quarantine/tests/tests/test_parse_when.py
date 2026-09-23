# ruff: noqa: DTZ001
import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.tools import _parse_when_at


class ParseWhenTest(unittest.TestCase):
    def parse(self, spec, base):
        return _parse_when_at(spec, base)

    def test_24h_hhmm(self):
        base = datetime(2026, 9, 3, 10, 0, 0)
        self.assertEqual(self.parse("14:30", base), "2026-09-03 14:30:00")
        # Past time rolls to tomorrow.
        self.assertEqual(self.parse("09:00", base), "2026-09-04 09:00:00")

    def test_12h_am_pm(self):
        base = datetime(2026, 9, 3, 10, 0, 0)
        self.assertEqual(self.parse("2:30pm", base), "2026-09-03 14:30:00")
        self.assertEqual(self.parse("2:30 pm", base), "2026-09-03 14:30:00")
        self.assertEqual(self.parse("1pm", base), "2026-09-03 13:00:00")
        self.assertEqual(self.parse("12pm", base), "2026-09-03 12:00:00")
        # Morning that already passed rolls to tomorrow.
        self.assertEqual(self.parse("9am", base), "2026-09-04 09:00:00")
        # Midnight rolls to next day.
        self.assertEqual(self.parse("12am", base), "2026-09-04 00:00:00")
        self.assertEqual(self.parse("12:00am", base), "2026-09-04 00:00:00")

    def test_relative_in(self):
        base = datetime(2026, 9, 3, 10, 0, 0)
        self.assertEqual(self.parse("in 20 minutes", base), "2026-09-03 10:20:00")
        self.assertEqual(self.parse("in 1 hour", base), "2026-09-03 11:00:00")
        self.assertEqual(self.parse("in 30 sec", base), "2026-09-03 10:00:30")
        self.assertEqual(self.parse("in an hour", base), "2026-09-03 11:00:00")
        self.assertEqual(self.parse("in half an hour", base), "2026-09-03 10:30:00")
        self.assertEqual(self.parse("in a minute", base), "2026-09-03 10:01:00")
        self.assertEqual(self.parse("in half a minute", base), "2026-09-03 10:00:30")

    def test_tomorrow(self):
        base = datetime(2026, 9, 3, 10, 0, 0)
        self.assertEqual(self.parse("tomorrow 14:30", base), "2026-09-04 14:30:00")
        self.assertEqual(self.parse("tomorrow at 9am", base), "2026-09-04 09:00:00")
        self.assertEqual(self.parse("tomorrow 9:15", base), "2026-09-04 09:15:00")
        self.assertEqual(self.parse("tomorrow at 2:30pm", base), "2026-09-04 14:30:00")

    def test_iso(self):
        base = datetime(2026, 9, 3, 10, 0, 0)
        self.assertEqual(self.parse("2026-09-05 08:00", base), "2026-09-05 08:00:00")

    def test_empty_returns_now(self):
        base = datetime(2026, 9, 3, 10, 0, 0)
        self.assertEqual(self.parse("", base), "2026-09-03 10:00:00")


if __name__ == "__main__":
    unittest.main()