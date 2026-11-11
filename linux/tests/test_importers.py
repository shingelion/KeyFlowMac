import unittest

from keyflow import importers as imp
from keyflow.core import Shortcut


class JsonTests(unittest.TestCase):
    def test_parses_shortcuts_schema(self):
        r = imp.parse_json('{"shortcuts":[{"trigger":";addr","expansion":"123 Main St","group":"Personal"},'
                           '{"trigger":";sig","expansion":"Best, Erwan","enabled":false}]}')
        self.assertEqual(len(r.shortcuts), 2)
        self.assertEqual(r.shortcuts[0].shortcut.group, "Personal")
        self.assertFalse(r.shortcuts[1].shortcut.enabled)

    def test_bare_array(self):
        self.assertEqual(len(imp.parse_json('[{"trigger":"a","expansion":"b"}]').shortcuts), 1)

    def test_round_trip(self):
        original = [Shortcut(";a", "b\nc", group="g", folder="Work", enabled=False)]
        again = [i.shortcut for i in imp.parse_json(imp.to_json(original)).shortcuts]
        self.assertEqual(original, again)

    def test_empty_fields_flagged(self):
        r = imp.parse_json('{"shortcuts":[{"trigger":"","expansion":"b"},{"trigger":";a","expansion":""}]}')
        self.assertEqual([s.reason for s in r.skipped], ["Empty trigger", "Empty expansion"])

    def test_invalid_json_and_missing_array(self):
        self.assertTrue(imp.parse_json("{nope").skipped[0].reason.startswith("Invalid JSON"))
        self.assertEqual(imp.parse_json("{}").skipped[0].reason, 'No "shortcuts" array found in JSON.')

    def test_malformed_row(self):
        self.assertEqual(imp.parse_json('["x"]').skipped[0].reason, "Malformed row")


class CsvTests(unittest.TestCase):
    def test_header_and_quoted_commas(self):
        r = imp.parse_csv('trigger,expansion,group\n;addr,"123 Main St, Apt 4",Personal\n')
        self.assertEqual(r.shortcuts[0].shortcut.expansion, "123 Main St, Apt 4")

    def test_missing_expansion(self):
        r = imp.parse_csv("trigger,expansion\n;onlytrigger,\n")
        self.assertEqual(r.skipped[0].reason, "Empty expansion")

    def test_malformed(self):
        self.assertEqual(imp.parse_csv("trigger,expansion\nnocomma\n").skipped[0].reason, "Malformed CSV row")

    def test_folder_column_and_skipped_export(self):
        r = imp.parse_csv("trigger,expansion,group,enabled,folder\n;a,b,,true,Work\n")
        self.assertEqual(r.shortcuts[0].folder_name, "Work")
        out = imp.skipped_to_csv([imp.SkippedRow(2, ";x", "", "", "", "", ";x,", "Empty expansion")])
        self.assertIn("reason", out)
        self.assertIn("Empty expansion", out)

    def test_round_trip_multiline(self):
        original = [Shortcut(";a", 'line1,\n"line2"', group="g", folder="F")]
        again = [i.shortcut for i in imp.parse_csv(imp.to_csv(original)).shortcuts]
        self.assertEqual(again[0].expansion, original[0].expansion)
        self.assertEqual(again[0].folder, "F")

    def test_empty_file(self):
        self.assertEqual(imp.parse_csv("").skipped[0].reason, "File is empty.")


class ResolverTests(unittest.TestCase):
    def _result(self, *pairs):
        return imp.ImportResult([
            imp.ImportedShortcut(Shortcut(t, e), None, i + 1, f"{t},{e}") for i, (t, e) in enumerate(pairs)
        ])

    def test_duplicate_in_batch(self):
        r = imp.resolve_import(self._result((";dup", "one"), (";DUP", "two")), lambda t: None, False)
        self.assertEqual([s.expansion for s in r.to_save], ["one"])
        self.assertEqual(r.skipped[0].reason, "Duplicate trigger")

    def test_existing_skipped_unless_overwrite(self):
        existing = Shortcut(";a", "old")
        res = self._result((";a", "new"))
        self.assertEqual(imp.resolve_import(res, lambda t: existing, False).to_save, [])
        self.assertEqual(imp.resolve_import(res, lambda t: existing, True).to_save[0].expansion, "new")


if __name__ == "__main__":
    unittest.main()
