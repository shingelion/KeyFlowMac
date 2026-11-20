import tempfile
import unittest
from pathlib import Path

from keyflow.core import Folder, Shortcut
from keyflow.library import LibraryService
from keyflow.store import Library, Store
from tests.test_sync import Device, SYNC_FILE_NAME, SyncFile


class LibraryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name) / "kf")
        self.svc = LibraryService(self.store)

    def triggers(self):
        return {s.trigger: s for s in self.svc.load().shortcuts}

    def test_add_edit_validation(self):
        self.assertEqual(self.svc.save_shortcut(None, Shortcut("  ", "x")), "Trigger can't be empty")
        self.assertEqual(self.svc.save_shortcut(None, Shortcut(";a", "")), "Expansion can't be empty")
        self.assertIsNone(self.svc.save_shortcut(None, Shortcut(" ;a ", "one", group="  g ", folder="Work")))
        a = self.triggers()[";a"]
        self.assertEqual((a.group, a.folder), ("g", "Work"))
        self.assertIn("already used", self.svc.save_shortcut(None, Shortcut(";A", "dup")))
        # editing keeps identity, may rename the trigger, and may not collide with another
        self.assertIsNone(self.svc.save_shortcut(None, Shortcut(";b", "two")))
        self.assertIn("already used", self.svc.save_shortcut(a, Shortcut(";b", "x")))
        self.assertIsNone(self.svc.save_shortcut(a, Shortcut(";a2", "edited", sync_id="forged")))
        self.assertEqual(sorted(self.triggers()), [";a2", ";b"])
        self.assertIsNone(self.triggers()[";a2"].sync_id)          # never trusts a caller-supplied id

    def test_edit_preserves_sync_identity(self):
        self.svc.save_shortcut(None, Shortcut(";a", "one"))
        lib = self.svc.load()
        lib.shortcuts[0].sync_id = "id-1"
        self.store.save_library(lib)
        ref = self.svc.load().shortcuts[0]
        self.svc.save_shortcut(ref, Shortcut(";renamed", "two"))
        self.assertEqual(self.triggers()[";renamed"].sync_id, "id-1")

    def test_enable_delete_move(self):
        for t in (";a", ";b", ";c"):
            self.svc.save_shortcut(None, Shortcut(t, t))
        a, b, c = (self.triggers()[t] for t in (";a", ";b", ";c"))
        self.svc.set_enabled(a, False)
        self.assertFalse(self.triggers()[";a"].enabled)
        self.svc.move_to_folder([b, c], "Work")
        self.assertEqual({s.folder for s in self.svc.load().shortcuts if s.trigger != ";a"}, {"Work"})
        self.assertEqual([f.name for f in self.svc.load().folders], ["Work"])    # registry auto-grows
        self.svc.delete_shortcuts([a, b])
        self.assertEqual(sorted(self.triggers()), [";c"])
        self.assertIn("no longer exists", self.svc.set_enabled(a, True))

    def test_folder_lifecycle(self):
        self.assertEqual(self.svc.create_folder("  "), "Folder name can't be empty")
        self.assertIsNone(self.svc.create_folder("Work", "#E53935"))
        self.assertIn("already exists", self.svc.create_folder("work"))
        self.svc.save_shortcut(None, Shortcut(";a", "x", folder="Work"))
        self.assertIsNone(self.svc.rename_folder("Work", "Jobs"))
        self.assertEqual(self.svc.load().shortcuts[0].folder, "Jobs")
        self.assertEqual([(f.name, f.color_tag) for f in self.svc.load().folders], [("Jobs", "#E53935")])
        self.svc.create_folder("Home")
        self.assertIn("already exists", self.svc.rename_folder("Jobs", "home"))
        self.svc.set_folder_color("Jobs", "#1E88E5")
        self.assertEqual(self.svc.load().folders[0].color_tag, "#1E88E5")
        self.svc.delete_folder("Jobs", delete_shortcuts=False)
        self.assertEqual((self.svc.load().shortcuts[0].folder, [f.name for f in self.svc.load().folders]), (None, ["Home"]))
        self.svc.save_shortcut(None, Shortcut(";b", "y", folder="Home"))
        self.svc.delete_folder("Home", delete_shortcuts=True)
        self.assertEqual(sorted(self.triggers()), [";a"])

    def test_empty_folders_and_colours_survive_shortcut_only_saves(self):
        self.svc.create_folder("Empty", "#43A047")
        self.store.save_shortcuts([Shortcut(";a", "x")])             # e.g. the CLI rewriting the list
        self.assertEqual([(f.name, f.color_tag) for f in self.store.load_library().folders], [("Empty", "#43A047")])

    def test_import_export(self):
        out = self.svc.import_text('{"shortcuts":[{"trigger":";a","expansion":"1","folder":"F"},{"trigger":"","expansion":"x"}]}', False)
        self.assertEqual((out.imported, [r.reason for r in out.skipped]), (1, ["Empty trigger"]))
        out = self.svc.import_text("trigger,expansion\n;a,2\n;b,3\n", False)
        self.assertEqual((out.imported, [r.reason for r in out.skipped]), (1, ["Duplicate trigger"]))
        out = self.svc.import_text("trigger,expansion\n;a,2\n", True)
        self.assertEqual((out.imported, self.triggers()[";a"].expansion), (1, "2"))
        self.assertIn(";b", self.svc.export_text("csv"))
        self.assertNotIn("syncId", self.svc.export_text("json"))


class FolderSyncTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "shared").mkdir()
        self.t = [1_000_000]
        self.a = Device(self.root, "a", self.root / "shared", self.t)
        self.b = Device(self.root, "b", self.root / "shared", self.t)

    def tick(self):
        self.t[0] += 1000

    def folders(self, dev):
        return [(f.name, f.color_tag) for f in dev.store.load_library().folders]

    def remote(self):
        return SyncFile.decode((self.root / "shared" / SYNC_FILE_NAME).read_bytes())

    def act(self, dev, fn):
        fn(LibraryService(dev.store))
        dev.last_edit = self.t[0]

    def test_empty_folder_colour_rename_and_delete_travel_between_devices(self):
        self.act(self.a, lambda s: s.create_folder("Work", "#E53935"))
        self.a.sync(); self.tick(); self.b.sync()
        self.assertEqual(self.folders(self.b), [("Work", "#E53935")])          # even though it is empty

        self.tick()
        self.act(self.a, lambda s: s.save_shortcut(None, Shortcut(";a", "x", folder="Work")))
        self.act(self.a, lambda s: s.rename_folder("Work", "Jobs"))
        self.act(self.a, lambda s: s.set_folder_color("Jobs", "#1E88E5"))
        self.a.sync(); self.tick(); self.b.sync()
        self.assertEqual(self.folders(self.b), [("Jobs", "#1E88E5")])          # a rename, not a new folder
        self.assertEqual(self.b.shortcuts[";a"].folder, "Jobs")
        self.assertEqual(len(self.remote().folders), 1)

        self.tick()
        self.act(self.b, lambda s: s.delete_folder("Jobs", delete_shortcuts=False))
        self.b.sync(); self.tick(); self.a.sync()
        self.assertEqual(self.folders(self.a), [])
        self.assertIsNone(self.a.shortcuts[";a"].folder)
        self.assertEqual([f.deleted_at is not None for f in self.remote().folders], [True])

    def test_folder_sort_order_and_first_contact_adoption(self):
        self.act(self.a, lambda s: (s.create_folder("Home"), s.create_folder("Work")))
        self.act(self.b, lambda s: s.create_folder("work"))                    # same folder, other case
        self.a.sync(); self.tick(); self.b.sync(); self.tick(); self.a.sync()
        self.assertEqual(len(self.remote().folders), 2)                        # adopted, not duplicated
        self.assertEqual([n.lower() for n, _ in self.folders(self.b)].count("work"), 1)


if __name__ == "__main__":
    unittest.main()
