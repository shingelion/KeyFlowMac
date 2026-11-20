import json
import os
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from keyflow import cli
from keyflow.core import Shortcut
from keyflow.store import Store
from keyflow.sync import (
    DELETE, IGNORE, UPSERT, FolderEntry, ShortcutEntry, SyncEngine, SyncFile, SyncRunner, SyncState,
    pair_identities, resolve, SYNC_FILE_NAME,
)


def entry(sync_id="a", modified=100, deleted=None, trigger=";t", expansion="e", **kw):
    return ShortcutEntry(sync_id, trigger, expansion, False, None, True, modified, modified, deleted, **kw)


class MergeDecisionTests(unittest.TestCase):
    """Mirrors SyncMergerTests.swift: strictly newer wins, local wins ties."""

    def test_unseen_live_is_upserted(self):
        self.assertEqual(resolve(entry(modified=100), None), UPSERT)

    def test_unseen_tombstone_is_recorded(self):
        self.assertEqual(resolve(entry(modified=200, deleted=200), None), DELETE)

    def test_older_incoming_ignored(self):
        self.assertEqual(resolve(entry(modified=100), 200), IGNORE)

    def test_newer_incoming_upserted(self):
        self.assertEqual(resolve(entry(modified=200), 100), UPSERT)

    def test_ties_go_to_local(self):
        self.assertEqual(resolve(entry(modified=100), 100), IGNORE)

    def test_newer_tombstone_deletes(self):
        self.assertEqual(resolve(entry(modified=200, deleted=200), 100), DELETE)

    def test_older_tombstone_loses_to_newer_live(self):
        self.assertEqual(resolve(entry(modified=100, deleted=100), 200), IGNORE)

    def test_newer_live_resurrects_older_tombstone(self):
        self.assertEqual(resolve(entry(modified=200), 100), UPSERT)

    def test_stale_date_modified_does_not_hide_a_newer_delete(self):
        self.assertEqual(resolve(entry(modified=50, deleted=300), 100), DELETE)


class AdopterTests(unittest.TestCase):
    def test_pairs_by_normalised_key(self):
        self.assertEqual(pair_identities([("in1", " ;Addr ")], [("loc1", ";addr")]), [("in1", "loc1")])

    def test_each_local_claimed_once_and_blank_never_matches(self):
        pairs = pair_identities([("a", ";x"), ("b", ";x"), ("c", "")], [("l", ";x"), ("m", "")])
        self.assertEqual(pairs, [("a", "l")])


class WireFormatTests(unittest.TestCase):
    def test_round_trip_writes_explicit_nulls(self):
        f = SyncFile([FolderEntry("f1", "Work", None, 2, 1, 2, None)], [entry("s1", folder_sync_id="f1")])
        raw = json.loads(f.encode())
        self.assertIsNone(raw["shortcuts"][0]["deletedAt"])
        self.assertIsNone(raw["shortcuts"][0]["group"])
        self.assertIsNone(raw["folders"][0]["colorTag"])
        self.assertEqual(SyncFile.decode(f.encode()).shortcuts, f.shortcuts)

    def test_lenient_decode(self):
        doc = {"version": 1, "folders": [{"syncId": " "}, "junk"], "shortcuts": [
            {"syncId": "ok"}, {"trigger": "no id"}, 7, {"syncId": "x", "enabled": "yes", "dateModified": 12.0}]}
        f = SyncFile.decode(json.dumps(doc).encode())
        self.assertEqual([s.sync_id for s in f.shortcuts], ["ok", "x"])
        self.assertTrue(f.shortcuts[0].enabled)           # missing -> default true
        self.assertEqual(f.shortcuts[1].date_modified, 12)  # integral float accepted
        self.assertEqual(f.folders, [])

    def test_unparseable_raises(self):
        for bad in (b"", b"{", b"[1]", b"\xff"):
            with self.assertRaises(ValueError):
                SyncFile.decode(bad)

    def test_equivalence_ignores_order_and_version(self):
        a = SyncFile([], [entry("a"), entry("b")], version=1)
        b = SyncFile([], [entry("b"), entry("a")], version=2)
        self.assertTrue(a.is_equivalent(b))


class Device:
    """One computer: its own config dir and clock, sharing a Syncthing folder."""

    def __init__(self, root: Path, name: str, shared: Path, clock: list):
        self.store = Store(root / name / "keyflow")
        self.clock = clock
        self.last_edit = clock[0]  # stands in for the mtime of shortcuts.json
        self.engine = SyncEngine(self.store, shared, clock_ms=lambda: clock[0],
                                 file_mtime_ms=lambda: self.last_edit)

    def set(self, *shortcuts):
        self.store.save_shortcuts(list(shortcuts))
        self.last_edit = self.clock[0]

    def sync(self):
        r = self.engine.sync_once()
        assert r.ok, r.message
        return r

    @property
    def shortcuts(self):
        return {s.trigger: s for s in self.store.load_shortcuts()}


class TwoDeviceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.shared = self.root / "shared"
        self.shared.mkdir()
        self.t = [1_000_000]
        self.a = Device(self.root, "a", self.shared, self.t)
        self.b = Device(self.root, "b", self.shared, self.t)

    def tick(self, ms=1000):
        self.t[0] += ms

    def remote(self) -> SyncFile:
        return SyncFile.decode((self.shared / SYNC_FILE_NAME).read_bytes())

    def test_add_propagates_and_is_idempotent(self):
        self.a.set(Shortcut(";addr", "123 Main St", group="Home", folder="Work"))
        self.a.sync()
        self.tick()
        self.b.sync()
        got = self.b.shortcuts[";addr"]
        self.assertEqual((got.expansion, got.group, got.folder), ("123 Main St", "Home", "Work"))
        self.assertEqual(got.sync_id, self.a.shortcuts[";addr"].sync_id)

        before = (self.shared / SYNC_FILE_NAME).stat().st_mtime_ns
        self.tick()
        r = self.b.sync()
        self.a.sync()
        self.assertFalse(r.wrote_remote or r.changed_local)
        self.assertEqual((self.shared / SYNC_FILE_NAME).stat().st_mtime_ns, before)  # no churn

    def test_edit_and_delete_propagate(self):
        self.a.set(Shortcut(";a", "one"), Shortcut(";b", "two"))
        self.a.sync(); self.tick(); self.b.sync(); self.tick()

        self.b.set(*[replace(s, expansion="ONE") if s.trigger == ";a" else s for s in self.b.store.load_shortcuts()])
        self.b.sync(); self.tick(); self.a.sync()
        self.assertEqual(self.a.shortcuts[";a"].expansion, "ONE")

        self.tick()
        self.a.set(*[s for s in self.a.store.load_shortcuts() if s.trigger != ";b"])
        self.a.sync(); self.tick(); self.b.sync()
        self.assertNotIn(";b", self.b.shortcuts)
        self.assertEqual([s.deleted_at is not None for s in self.remote().shortcuts if s.trigger == ";b"], [True])

    def test_absence_from_the_file_is_not_a_delete(self):
        self.a.set(Shortcut(";a", "one"))
        self.a.sync(); self.tick(); self.b.sync()
        (self.shared / SYNC_FILE_NAME).write_text('{"version":1,"folders":[],"shortcuts":[]}')
        self.tick(); self.b.sync()
        self.assertIn(";a", self.b.shortcuts)

    def test_concurrent_edits_newest_wins_on_both_sides(self):
        self.a.set(Shortcut(";a", "base"))
        self.a.sync(); self.tick(); self.b.sync(); self.tick()
        self.a.set(replace(self.a.shortcuts[";a"], expansion="from A"))
        self.tick()
        self.b.set(replace(self.b.shortcuts[";a"], expansion="from B"))  # later
        self.b.sync()      # B publishes first
        self.tick(); self.a.sync()   # A reads B's newer edit and keeps it (A's older local edit loses)
        self.tick(); self.b.sync()
        self.assertEqual(self.a.shortcuts[";a"].expansion, "from B")
        self.assertEqual(self.b.shortcuts[";a"].expansion, "from B")

    def test_newer_edit_resurrects_a_deleted_shortcut(self):
        self.a.set(Shortcut(";a", "one"))
        self.a.sync(); self.tick(); self.b.sync(); self.tick()
        self.a.set(); self.a.sync()           # A deletes
        self.tick()
        self.b.set(replace(self.b.shortcuts[";a"], expansion="edited after delete"))
        self.b.sync()                          # B's edit is newer than the tombstone
        self.tick(); self.a.sync()
        self.assertEqual(self.a.shortcuts[";a"].expansion, "edited after delete")

    def test_first_contact_adopts_identity_instead_of_duplicating(self):
        self.a.set(Shortcut(";addr", "A text"), Shortcut(";only-a", "x"))
        self.b.set(Shortcut(";addr", "B text"), Shortcut(";only-b", "y"))
        self.a.sync(); self.tick(); self.b.sync(); self.tick(); self.a.sync()
        for dev in (self.a, self.b):
            self.assertEqual(sorted(dev.shortcuts), [";addr", ";only-a", ";only-b"])
        self.assertEqual(self.a.shortcuts[";addr"].sync_id, self.b.shortcuts[";addr"].sync_id)
        self.assertEqual(len([s for s in self.remote().shortcuts if s.trigger == ";addr"]), 1)

    def test_foreign_id_shapes_are_written_back_verbatim(self):
        hex_id = "0123456789abcdef0123456789abcdef"      # Android's migrated rows
        upper = "5A4543DC-1709-4A4A-9CBC-EA7D7BC02BA2"
        doc = SyncFile([], [entry(hex_id, 5, trigger=";h"), entry(upper, 5, trigger=";u")])
        (self.shared / SYNC_FILE_NAME).write_bytes(doc.encode())
        self.a.sync(); self.tick()
        self.a.set(*[replace(s, expansion="changed") for s in self.a.store.load_shortcuts()])
        self.a.sync()
        ids = {s.sync_id: s.expansion for s in self.remote().shortcuts}
        self.assertEqual(ids, {hex_id: "changed", upper: "changed"})

    def test_edit_is_newer_than_what_it_replaces_even_if_another_clock_runs_ahead(self):
        far = self.t[0] + 10_000_000                       # B's clock is ~3 hours ahead
        (self.shared / SYNC_FILE_NAME).write_bytes(
            SyncFile([], [entry("id1", far, trigger=";a", expansion="from the future")]).encode())
        self.a.sync()
        self.tick()
        self.a.set(replace(self.a.shortcuts[";a"], expansion="my edit"))
        self.a.sync()
        self.assertEqual([s.expansion for s in self.remote().shortcuts], ["my edit"])
        self.assertGreater(self.remote().shortcuts[0].date_modified, far)

    def test_hand_edit_and_missing_sync_id_are_detected(self):
        self.a.set(Shortcut(";a", "one"))
        self.a.sync(); self.tick()
        sid = self.a.shortcuts[";a"].sync_id
        # user edits shortcuts.json by hand: drops the syncId field and changes the text
        self.a.store.shortcuts_path.write_text(
            json.dumps({"shortcuts": [{"trigger": ";a", "expansion": "edited by hand"}]}))
        self.a.sync()
        self.assertEqual(self.a.shortcuts[";a"].sync_id, sid)       # same record, not delete+add
        self.assertEqual([s.expansion for s in self.remote().shortcuts], ["edited by hand"])

    def test_bootstrap_does_not_let_stale_local_data_beat_newer_remote(self):
        # remote has a newer edit; this device's file is older and has never synced
        self.a.set(Shortcut(";a", "local old"))   # edited at t=1_000_000, remote edit is later
        (self.shared / SYNC_FILE_NAME).write_bytes(
            SyncFile([], [entry("id1", 2_000_000, trigger=";a", expansion="remote newer")]).encode())
        self.a.sync()
        self.assertEqual(self.a.shortcuts[";a"].expansion, "remote newer")

    def test_folders_sync_and_remote_folder_delete_uncategorizes(self):
        self.a.set(Shortcut(";a", "x", folder="Work"))
        self.a.sync(); self.tick(); self.b.sync()
        self.assertEqual(self.b.shortcuts[";a"].folder, "Work")
        # another device deletes the folder
        raw = json.loads((self.shared / SYNC_FILE_NAME).read_text())
        self.tick(); self.tick()
        raw["folders"][0]["deletedAt"] = raw["folders"][0]["dateModified"] = self.t[0]
        (self.shared / SYNC_FILE_NAME).write_text(json.dumps(raw))
        self.b.sync()
        self.assertIsNone(self.b.shortcuts[";a"].folder)
        m = (self.shared / SYNC_FILE_NAME).stat().st_mtime_ns
        self.tick(); r = self.b.sync()             # and it must not ping-pong afterwards
        self.assertFalse(r.wrote_remote)
        self.assertEqual((self.shared / SYNC_FILE_NAME).stat().st_mtime_ns, m)

    def test_fresh_unreadable_file_is_not_overwritten_but_stale_one_is(self):
        self.a.set(Shortcut(";a", "one"))
        path = self.shared / SYNC_FILE_NAME
        path.write_text("{ half written")
        r = self.a.sync()
        self.assertTrue(r.retry)
        self.assertEqual(path.read_text(), "{ half written")
        os.utime(path, (time.time() - 60, time.time() - 60))
        r = self.a.sync()
        self.assertTrue(r.wrote_remote)
        self.assertEqual([s.trigger for s in self.remote().shortcuts], [";a"])

    def test_no_temp_file_left_and_missing_folder_reported(self):
        self.a.set(Shortcut(";a", "one"))
        self.a.sync()
        self.assertEqual([p.name for p in self.shared.iterdir()], [SYNC_FILE_NAME])
        gone = Device(self.root, "c", self.root / "nope", self.t)
        r = gone.engine.sync_once()
        self.assertFalse(r.ok)
        self.assertIn("does not exist", r.message)

    def test_corrupt_local_shortcuts_are_never_overwritten(self):
        self.a.store.directory.mkdir(parents=True)
        self.a.store.shortcuts_path.write_text("garbage")
        self.assertFalse(self.a.engine.sync_once().ok)
        self.assertEqual(self.a.store.shortcuts_path.read_text(), "garbage")
        self.assertFalse((self.shared / SYNC_FILE_NAME).exists())

    def test_old_tombstones_are_pruned(self):
        self.a.set(Shortcut(";a", "one")); self.a.sync(); self.tick()
        self.a.set(); self.a.sync()
        self.assertEqual(len(SyncState.load(self.a.store.sync_state_path).tomb_shortcuts), 1)
        self.t[0] += 31 * 24 * 3600 * 1000
        self.a.sync()
        self.assertEqual(SyncState.load(self.a.store.sync_state_path).tomb_shortcuts, {})
        self.assertEqual(self.remote().shortcuts, [])        # gone from the file too, not re-adopted
        self.b.sync()                                         # and a late device does not bring it back
        self.assertEqual(self.b.shortcuts, {})


class RunnerTests(unittest.TestCase):
    def test_schedule_debounce_poll_and_retry(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "shared").mkdir()
            store = Store(root / "cfg")
            store.save_shortcuts([Shortcut(";a", "one")])
            now = [0.0]
            runner = SyncRunner(store, clock=lambda: now[0])
            calls = []
            real = SyncEngine.sync_once

            def counting(self):
                calls.append(now[0])
                return real(self)

            with mock.patch.object(SyncEngine, "sync_once", counting):
                runner.tick()
                self.assertEqual(calls, [])                   # disabled: nothing happens
                runner.configure(True, str(root / "shared"))
                runner.tick()
                self.assertEqual(calls, [0.0])                 # startup pass
                now[0] = 1.0; runner.tick()
                self.assertEqual(len(calls), 1)                # own write does not retrigger

                store.save_shortcuts([Shortcut(";a", "one"), Shortcut(";b", "two")])  # local edit
                now[0] = 2.0; runner.tick()
                self.assertEqual(len(calls), 1)                # debounced
                now[0] = 4.5; runner.tick()
                self.assertEqual(len(calls), 2)

                (root / "shared" / SYNC_FILE_NAME).touch()     # remote changed
                now[0] = 10.0; runner.tick()
                self.assertEqual(len(calls), 3)

                runner.configure(False, "")
                now[0] = 99.0; runner.tick()
                self.assertEqual(len(calls), 3)


class CliSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.tmp.name})
        env.start()
        self.addCleanup(env.stop)
        self.shared = Path(self.tmp.name) / "Sync"

    def run_cli(self, *argv):
        import contextlib, io
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_setup_now_status_off(self):
        self.assertEqual(self.run_cli("sync", "now")[0], 1)                   # not set up
        self.assertEqual(self.run_cli("sync", "setup", str(self.shared))[0], 1)   # missing folder
        self.run_cli("add", ";a", "one")
        code, out, _ = self.run_cli("sync", "setup", str(self.shared), "--create")
        self.assertEqual(code, 0, out)
        doc = json.loads((self.shared / SYNC_FILE_NAME).read_text())
        self.assertEqual([s["trigger"] for s in doc["shortcuts"]], [";a"])
        self.assertIn("enabled:     yes", self.run_cli("sync", "status")[1])
        self.run_cli("remove", ";a")
        self.assertEqual(self.run_cli("sync", "now")[0], 0)
        doc = json.loads((self.shared / SYNC_FILE_NAME).read_text())
        self.assertIsNotNone(doc["shortcuts"][0]["deletedAt"])
        self.assertEqual(self.run_cli("sync", "off")[0], 0)
        self.assertIn("enabled:     no", self.run_cli("sync", "status")[1])

    def test_import_overwrite_and_force_keep_identity_and_exports_stay_clean(self):
        self.run_cli("add", ";a", "one")
        self.run_cli("sync", "setup", str(self.shared), "--create")
        sid = Store().load_shortcuts()[0].sync_id
        self.assertTrue(sid)
        self.run_cli("add", ";a", "two", "--force")
        self.assertEqual(Store().load_shortcuts()[0].sync_id, sid)
        src = Path(self.tmp.name) / "in.json"
        src.write_text(json.dumps({"shortcuts": [{"trigger": ";a", "expansion": "three", "syncId": "evil"}]}))
        self.run_cli("import", str(src), "--overwrite")
        self.assertEqual(Store().load_shortcuts()[0].sync_id, sid)
        self.assertNotIn("syncId", self.run_cli("export")[1])


if __name__ == "__main__":
    unittest.main()
