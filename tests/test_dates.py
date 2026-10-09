import unittest

from sel.dates import resolve_due

# Wednesday 7 October 2026, Nepal time.
CALL = "2026-10-07T14:00:00+05:45"


def due(phrase, week_starts_on="sunday"):
    return resolve_due(phrase, CALL, week_starts_on)


class ExactDays(unittest.TestCase):
    def test_relative_days_english_and_nepali(self):
        cases = {
            "today": "2026-10-07",
            "aaja nai": "2026-10-07",
            "tomorrow": "2026-10-08",
            "bholi beluka samma": "2026-10-08",
            "भोलि": "2026-10-08",
            "day after tomorrow": "2026-10-09",
            "parsi": "2026-10-09",
            "in 3 days": "2026-10-10",
            "dui din ma": "2026-10-09",
        }
        for phrase, expected in cases.items():
            with self.subTest(phrase=phrase):
                r = due(phrase)
                self.assertEqual(r.status, "exact")
                self.assertEqual(r.date, expected)

    def test_weekday_later_this_week(self):
        for phrase in ("Friday", "on friday", "sukrabar", "शुक्रबार"):
            with self.subTest(phrase=phrase):
                self.assertEqual(due(phrase).date, "2026-10-09")

    def test_weekday_next_week_explicit(self):
        # Nepal's week starts on Sunday, so "next week" is 11–17 October.
        self.assertEqual(due("next week Tuesday").date, "2026-10-13")
        self.assertEqual(due("arko hapta sombar").date, "2026-10-12")

    def test_times(self):
        self.assertEqual(due("Thursday at 3pm").time, "15:00")
        self.assertEqual(due("bholi diuso 3 baje").time, "15:00")
        self.assertEqual(due("bholi bihana 10:30").time, "10:30")
        self.assertEqual(due("bholi beluka").part_of_day, "evening")


class RefusesToGuess(unittest.TestCase):
    def test_vague_phrases(self):
        for phrase in ("sometime next week", "soon", "pachhi", "kunai bela", "later this month"):
            with self.subTest(phrase=phrase):
                r = due(phrase)
                self.assertEqual(r.status, "ambiguous")
                self.assertIsNone(r.date)
                self.assertTrue(r.reason)

    def test_next_week_gives_window(self):
        r = due("sometime next week")
        self.assertEqual((r.window_start, r.window_end), ("2026-10-11", "2026-10-17"))
        r = due("sometime next week", week_starts_on="monday")
        self.assertEqual((r.window_start, r.window_end), ("2026-10-12", "2026-10-18"))

    def test_next_weekday_is_ambiguous(self):
        # Said on a Wednesday, "next Friday" could be the 9th or the 16th.
        r = due("next Friday")
        self.assertEqual(r.status, "ambiguous")
        self.assertEqual((r.window_start, r.window_end), ("2026-10-09", "2026-10-16"))
        self.assertEqual(due("arko sukrabar").status, "ambiguous")

    def test_same_weekday_as_call(self):
        self.assertEqual(due("Wednesday").status, "ambiguous")

    def test_two_days_named(self):
        self.assertEqual(due("bholi ki parsi").status, "ambiguous")
        self.assertEqual(due("Thursday or Friday").status, "ambiguous")

    def test_baje_without_part_of_day_keeps_date_but_not_time(self):
        r = due("bholi 3 baje")
        self.assertEqual(r.status, "exact")
        self.assertEqual(r.date, "2026-10-08")
        self.assertIsNone(r.time)
        self.assertIn("morning or afternoon", r.reason)

    def test_time_without_day(self):
        self.assertEqual(due("at 4pm").status, "ambiguous")

    def test_missing(self):
        self.assertEqual(due(None).status, "missing")
        self.assertEqual(due("  ").status, "missing")

    def test_absolute_dates_not_yet_supported(self):
        self.assertEqual(due("Kartik 5").status, "ambiguous")


if __name__ == "__main__":
    unittest.main()
