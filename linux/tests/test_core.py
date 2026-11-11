import datetime as dt
import unittest

from keyflow.core import (
    AppSettings, ExpansionEngine, Shortcut, adapt_case, format_icu, process_placeholders,
)


def make_engine() -> ExpansionEngine:
    e = ExpansionEngine()
    e.update_shortcuts([
        Shortcut(";addr", "123 Main St"),
        Shortcut(";a", "short"),
        Shortcut("sig", "Best, Erwan"),
    ])
    return e


class EngineTests(unittest.TestCase):
    def test_boundary_mode_requires_trailing_delimiter(self):
        e, s = make_engine(), AppSettings(expansion_key="boundaryKey")
        self.assertIsNone(e.find_match("Hello ;addr", s))
        m = e.find_match("Hello ;addr ", s)
        self.assertEqual(m.shortcut.trigger, ";addr")
        self.assertEqual((m.match_start, m.match_end), (6, 11))

    def test_immediate_mode_fires_without_delimiter(self):
        m = make_engine().find_match("Hello ;addr", AppSettings(expansion_key="immediate"))
        self.assertEqual(m.shortcut.trigger, ";addr")

    def test_longest_trigger_wins(self):
        m = make_engine().find_match(";addr", AppSettings(expansion_key="immediate"))
        self.assertEqual(m.shortcut.trigger, ";addr")

    def test_does_not_match_mid_word(self):
        e, s = make_engine(), AppSettings(expansion_key="immediate")
        self.assertIsNone(e.find_match("designsig", s))
        self.assertEqual(e.find_match("design sig", s).shortcut.trigger, "sig")

    def test_required_prefix(self):
        s = AppSettings(expansion_key="immediate", trigger_prefix=";")
        self.assertIsNone(make_engine().find_match("besig", s))

    def test_case_insensitive_by_default(self):
        s = AppSettings(case_sensitive=False, expansion_key="immediate")
        self.assertEqual(make_engine().find_match(";ADDR", s).shortcut.trigger, ";addr")

    def test_case_sensitive_rejects_mismatch(self):
        s = AppSettings(case_sensitive=True, expansion_key="immediate")
        self.assertIsNone(make_engine().find_match(";ADDR", s))

    def test_disabled_shortcuts_are_ignored(self):
        e = ExpansionEngine()
        e.update_shortcuts([Shortcut(";x", "y", enabled=False)])
        self.assertIsNone(e.find_match(";x", AppSettings(expansion_key="immediate")))

    def test_empty_input(self):
        self.assertIsNone(make_engine().find_match("", AppSettings()))
        self.assertIsNone(ExpansionEngine().find_match("abc ", AppSettings()))


class CaseAdapterTests(unittest.TestCase):
    def test_caps(self):
        self.assertEqual(adapt_case(";ADDR", "hello"), "HELLO")

    def test_title_case(self):
        self.assertEqual(adapt_case(";Addr", "hello there"), "Hello there")

    def test_lowercase_untouched(self):
        self.assertEqual(adapt_case(";addr", "hello"), "hello")

    def test_no_letters(self):
        self.assertEqual(adapt_case(";;", "hello"), "hello")


NOW = dt.datetime(2026, 3, 5, 14, 30, 0)


class PlaceholderTests(unittest.TestCase):
    def test_cursor(self):
        r = process_placeholders("Dear {cursor},\n\nBest", NOW)
        self.assertEqual(r.text, "Dear ,\n\nBest")
        self.assertEqual(r.cursor_offset, 5)

    def test_plain(self):
        r = process_placeholders("plain text", NOW)
        self.assertEqual((r.text, r.cursor_offset), ("plain text", None))

    def test_custom_patterns(self):
        self.assertEqual(process_placeholders("{date:yyyy-MM-dd} {time:HH:mm}", NOW).text, "2026-03-05 14:30")

    def test_defaults(self):
        self.assertEqual(process_placeholders("{date} | {time}", NOW).text, "Mar 5, 2026 | 2:30 PM")

    def test_icu_patterns(self):
        self.assertEqual(format_icu("EEEE d MMMM yy", NOW), "Thursday 5 March 26")
        self.assertEqual(format_icu("hh 'o''clock' a", NOW), "02 o'clock PM")
        self.assertEqual(format_icu("h:mm a", dt.datetime(2026, 1, 1, 0, 5)), "12:05 AM")


if __name__ == "__main__":
    unittest.main()
