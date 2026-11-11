import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from keyflow import cli
from keyflow.core import AppSettings, Shortcut
from keyflow.store import LinuxConfig, Store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "keyflow")

    def test_missing_files_give_defaults(self):
        self.assertEqual(self.store.load_shortcuts(), [])
        settings, linux = self.store.load_settings()
        self.assertEqual(settings, AppSettings())
        self.assertEqual(linux, LinuxConfig())

    def test_shortcuts_round_trip_and_private_permissions(self):
        data = [Shortcut(";a", "b", group="g", folder="f", enabled=False)]
        self.store.save_shortcuts(data)
        self.assertEqual(self.store.load_shortcuts(), data)
        self.assertEqual(oct(self.store.shortcuts_path.stat().st_mode & 0o777), "0o600")
        self.assertEqual(oct(self.store.directory.stat().st_mode & 0o777), "0o700")

    def test_corrupt_shortcuts_file_raises_instead_of_wiping(self):
        self.store.directory.mkdir(parents=True)
        self.store.shortcuts_path.write_text("{ not json")
        with self.assertRaises(ValueError):
            self.store.load_shortcuts()

    def test_settings_round_trip_and_bad_values_fall_back(self):
        s = AppSettings(case_sensitive=True, expansion_key="immediate", trigger_prefix=";")
        self.store.save_settings(s, LinuxConfig(xkb_layout="fr", input_devices=["AT"]))
        loaded, linux = self.store.load_settings()
        self.assertEqual(loaded, s)
        self.assertEqual((linux.xkb_layout, linux.input_devices), ("fr", ["AT"]))
        self.store.settings_path.write_text('{"expansionKey": "bogus", "caseSensitive": "yes", "injectDelayMs": -5}')
        loaded, linux = self.store.load_settings()
        self.assertEqual(loaded, AppSettings())
        self.assertEqual(linux.inject_delay_ms, LinuxConfig().inject_delay_ms)

    def test_fingerprint_changes_on_write(self):
        before = self.store.fingerprint()
        self.store.save_shortcuts([Shortcut("a", "b")])
        self.assertNotEqual(before, self.store.fingerprint())


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.tmp.name})
        env.start()
        self.addCleanup(env.stop)

    def run_cli(self, *argv, stdin=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            if stdin is not None:
                with mock.patch("sys.stdin", io.StringIO(stdin)):
                    code = cli.main(list(argv))
            else:
                code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_add_list_disable_remove(self):
        self.assertEqual(self.run_cli("add", ";addr", "123 Main St")[0], 0)
        code, out, _ = self.run_cli("list")
        self.assertIn(";addr", out)
        self.assertEqual(self.run_cli("add", ";ADDR", "x")[0], 1)  # case-insensitive duplicate
        self.assertEqual(self.run_cli("add", ";ADDR", "x", "--force")[0], 0)
        self.assertEqual(self.run_cli("disable", ";addr")[0], 0)
        self.assertIn("x ;ADDR", self.run_cli("list")[1])
        self.assertEqual(self.run_cli("remove", ";addr")[0], 0)
        self.assertEqual(self.run_cli("remove", ";addr")[0], 1)

    def test_multiline_expansion_from_stdin(self):
        self.run_cli("add", ";sig", "-", stdin="Best,\nErwan")
        _, out, _ = self.run_cli("list", "--json")
        self.assertIn("Best,\\nErwan", out)

    def test_import_export_and_skipped_report(self):
        src = Path(self.tmp.name) / "in.csv"
        src.write_text("trigger,expansion\n;a,one\n;a,two\n;b,\n")
        skipped = Path(self.tmp.name) / "skipped.csv"
        code, out, _ = self.run_cli("import", str(src), "--skipped-out", str(skipped))
        self.assertEqual(code, 0)
        self.assertIn("Imported 1 shortcut(s), skipped 2", out)
        self.assertIn("Duplicate trigger", skipped.read_text())
        code, out, _ = self.run_cli("export", "--format", "csv")
        self.assertIn(";a,one", out)

    def test_settings_get_set_and_validation(self):
        self.assertEqual(self.run_cli("settings", "expansionKey", "immediate")[0], 0)
        self.assertEqual(self.run_cli("settings", "expansionKey")[1].strip(), "immediate")
        self.assertEqual(self.run_cli("settings", "expansionKey", "nope")[0], 1)
        self.assertEqual(self.run_cli("settings", "caseSensitive", "maybe")[0], 1)
        self.assertEqual(self.run_cli("settings", "keyboard.layout", "fr")[0], 0)
        self.assertEqual(self.run_cli("settings", "inputDevices", "AT Translated,Keychron")[0], 0)
        self.assertEqual(self.run_cli("settings", "nonsense", "1")[0], 1)

    def test_pause_resume(self):
        self.run_cli("pause")
        self.assertEqual(self.run_cli("settings", "serviceEnabled")[1].strip(), "False")
        self.run_cli("resume")
        self.assertEqual(self.run_cli("settings", "serviceEnabled")[1].strip(), "True")

    def test_try_dry_run(self):
        self.run_cli("add", ";d", "Dear {cursor},")
        code, out, _ = self.run_cli("try", "hello ;d ")
        self.assertEqual(code, 0)
        self.assertIn("Dear ,", out)
        self.assertEqual(self.run_cli("try", "nothing here ")[0], 2)

    def test_corrupt_config_is_reported_not_overwritten(self):
        d = Path(self.tmp.name) / "keyflow"
        d.mkdir()
        (d / "shortcuts.json").write_text("garbage")
        code, _, err = self.run_cli("add", ";a", "b")
        self.assertEqual(code, 1)
        self.assertEqual((d / "shortcuts.json").read_text(), "garbage")


if __name__ == "__main__":
    unittest.main()
