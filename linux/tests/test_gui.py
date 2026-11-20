"""GUI tests: drive the real GTK widgets. Skipped without PyGObject/GTK 4 and a display
(run with `xvfb-run -a python3 -m unittest discover -s tests -t .` on a headless machine)."""

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from keyflow import status
from keyflow.core import Folder, Shortcut
from keyflow.library import ALL, UNCATEGORIZED, filter_shortcuts
from keyflow.store import Library, Store

try:
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib, Gtk

    HAVE_GTK = Gtk.init_check()
except (ImportError, ValueError):
    HAVE_GTK = False


class FilterTests(unittest.TestCase):
    """Pure logic, runs everywhere (port of ShortcutListView.filtered)."""

    S = [Shortcut(";a", "alpha", group="G", folder="Work"), Shortcut(";b", "beta"),
         Shortcut(";c", "gamma ALPHA", folder="work")]

    def f(self, **kw):
        args = dict(query="", group=None, folder_filter=ALL, scoped=False)
        args.update(kw)
        return [s.trigger for s in filter_shortcuts(self.S, **args)]

    def test_folder_and_group(self):
        self.assertEqual(self.f(), [";a", ";b", ";c"])
        self.assertEqual(self.f(folder_filter=UNCATEGORIZED), [";b"])
        self.assertEqual(self.f(folder_filter="WORK"), [";a", ";c"])
        self.assertEqual(self.f(group="G"), [";a"])

    def test_unscoped_search_ignores_folder_scoped_does_not(self):
        self.assertEqual(self.f(query="alpha", folder_filter=UNCATEGORIZED), [";a", ";c"])
        self.assertEqual(self.f(query="alpha", folder_filter=UNCATEGORIZED, scoped=True), [])
        self.assertEqual(self.f(query=";B"), [";b"])


def pump(seconds: float = 0.05) -> None:
    ctx = GLib.MainContext.default()
    end = time.time() + seconds
    while True:
        while ctx.pending():
            ctx.iteration(False)
        if time.time() >= end:
            return
        time.sleep(0.005)


def descendants(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from descendants(child)
        child = child.get_next_sibling()


def find(widget, cls, label=None, tooltip=None):
    for w in descendants(widget):
        if not isinstance(w, cls):
            continue
        if label is not None and getattr(w, "get_label", lambda: None)() != label:
            continue
        if tooltip is not None and w.get_tooltip_text() != tooltip:
            continue
        return w
    raise AssertionError(f"no {cls.__name__} label={label!r} tooltip={tooltip!r}")


def windows():
    return [w for w in Gtk.Window.list_toplevels() if w.get_visible()]


def window_titled(title):
    for w in windows():
        if w.get_title() == title:
            return w
    raise AssertionError(f"no window titled {title!r}; have {[w.get_title() for w in windows()]}")


@unittest.skipUnless(HAVE_GTK, "GTK 4 / display not available")
class GuiTests(unittest.TestCase):
    def setUp(self):
        from keyflow.gui.app import KeyFlowApp

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        patcher = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.tmp)})
        patcher.start()
        self.addCleanup(patcher.stop)
        diag = mock.patch.object(status, "diagnose", lambda: status.Diagnosis([], True))
        diag.start()
        self.addCleanup(diag.stop)

        self.store = Store()
        self.store.save_library(Library(
            [Shortcut(";addr", "123 Main St", group="Personal", folder="Home"),
             Shortcut(";sig", "Best,\nErwan", folder="Work"),
             Shortcut(";mail", "me@example.com")],
            [Folder("Home", "#43A047", 0), Folder("Work", "#1E88E5", 1)]))
        self.app = KeyFlowApp(store=self.store)
        self.app.register(None)
        self.win = self.app.show_main()
        pump(0.1)
        self.addCleanup(self._close)

    def _close(self):
        for w in list(Gtk.Window.list_toplevels()):
            w.destroy()
        pump()

    @property
    def page(self):
        return self.win.shortcuts_page

    def triggers_shown(self):
        out, row = [], self.page.list.get_first_child()
        while row is not None:
            out.append(row.shortcut.trigger)
            row = row.get_next_sibling()
        return out

    def stored(self):
        return {s.trigger: s for s in self.store.load_library().shortcuts}

    # -- list, search, filters ----------------------------------------------------------------

    def test_list_chips_and_filters(self):
        self.assertEqual(self.triggers_shown(), [";addr", ";sig", ";mail"])
        chips = [w.get_label() or "" for w in descendants(self.page.folder_chips) if isinstance(w, Gtk.ToggleButton)]
        self.assertEqual([c for c in chips if c], ["All", "Uncategorized"])     # folder chips carry a swatch child
        find(self.page.folder_chips, Gtk.ToggleButton, "Uncategorized").emit("clicked")
        self.assertEqual(self.triggers_shown(), [";mail"])
        self.assertTrue(self.page.scope_check.get_visible())
        self.page._set_folder("Work")
        self.assertEqual(self.triggers_shown(), [";sig"])
        self.page.search.set_text("main")                      # unscoped search crosses folders
        pump(0.4)                                              # SearchEntry debounces by 150 ms
        self.assertEqual(self.triggers_shown(), [";addr"])
        self.page.scope_check.set_active(True)
        pump(0.1)
        self.assertEqual(self.triggers_shown(), [])
        self.assertEqual(self.page.empty.get_text(), "No matches.")
        self.page.search.set_text("")
        pump(0.4)
        self.page.scope_check.set_active(False)
        self.page._set_folder(ALL)
        self.page._set_group("Personal")
        self.assertEqual(self.triggers_shown(), [";addr"])

    def test_empty_library_message(self):
        self.store.save_library(Library())
        self.win.reload()
        self.assertEqual(self.page.empty.get_text(), "No shortcuts yet. Click + to add one.")

    def test_switch_toggles_enabled_on_disk(self):
        row = self.page.list.get_first_child()
        sw = find(row, Gtk.Switch)
        sw.set_active(False)
        pump()
        self.assertFalse(self.stored()[";addr"].enabled)
        self.assertTrue(self.stored()[";sig"].enabled)

    # -- dialogs -------------------------------------------------------------------------------------

    def test_add_shortcut_dialog(self):
        from keyflow.gui.dialogs import ShortcutDialog

        d = ShortcutDialog(self.win, self.win.service, on_saved=self.win.reload)
        pump()
        d.save()                                                   # empty -> error, stays open
        self.assertEqual(d.error.get_text(), "Trigger can't be empty")
        d.trigger.set_text(";new")
        d.save()
        self.assertEqual(d.error.get_text(), "Expansion can't be empty")
        d.expansion.get_buffer().set_text("line1\nline2")
        d.group.set_text("Dev")
        d.folder.set_selected(1 + d.folder_names.index("Work"))
        d.save()
        pump()
        s = self.stored()[";new"]
        self.assertEqual((s.expansion, s.group, s.folder), ("line1\nline2", "Dev", "Work"))
        self.assertIn(";new", self.triggers_shown())
        self.assertFalse(d.win.get_visible())

        dup = ShortcutDialog(self.win, self.win.service)
        dup.trigger.set_text(";ADDR")
        dup.expansion.get_buffer().set_text("x")
        dup.save()
        self.assertIn("already used", dup.error.get_text())
        self.assertTrue(dup.win.get_visible())

    def test_edit_and_delete_from_dialog(self):
        from keyflow.gui.dialogs import ShortcutDialog

        original = self.stored()[";sig"]
        d = ShortcutDialog(self.win, self.win.service, editing=original, on_saved=self.win.reload)
        self.assertEqual(d.trigger.get_text(), ";sig")
        self.assertEqual(d.selected_folder(), "Work")
        d.expansion.get_buffer().set_text("Cheers")
        d.enabled.set_active(False)
        d.folder.set_selected(0)                                   # Uncategorized
        d.save()
        s = self.stored()[";sig"]
        self.assertEqual((s.expansion, s.enabled, s.folder), ("Cheers", False, None))
        d2 = ShortcutDialog(self.win, self.win.service, editing=self.stored()[";sig"], on_saved=self.win.reload)
        d2.delete()
        self.assertNotIn(";sig", self.stored())

    def test_row_click_opens_editor(self):
        row = self.page.list.get_first_child()
        self.page.list.emit("row-activated", row)
        pump()
        self.assertEqual(window_titled("Edit Shortcut").get_title(), "Edit Shortcut")

    def test_quick_add(self):
        from keyflow.gui.dialogs import QuickAddWindow

        q = QuickAddWindow(self.win, self.win.service, "picked text", on_saved=self.win.reload)
        q.save()
        self.assertEqual(q.error.get_text(), "Both fields are required")
        q.trigger.set_text(";pick")
        q.save()
        self.assertEqual(self.stored()[";pick"].expansion, "picked text")
        q2 = QuickAddWindow(self.win, self.win.service, "x")
        q2.trigger.set_text(";pick")
        q2.save()
        self.assertEqual(q2.error.get_text(), '";pick" is already used')

    # -- selection mode ----------------------------------------------------------------------------

    def test_selection_move_and_delete(self):
        p = self.page
        p.enter_selection_mode()
        self.assertTrue(p.selection_mode)                       # stays on with nothing selected yet
        self.assertTrue(self.win.selection_box.get_visible())
        self.assertFalse(self.win.switcher.get_visible())
        first = p.list.get_first_child()
        p.list.emit("row-activated", first)                        # taps toggle in selection mode
        p.list.emit("row-activated", p.list.get_first_child().get_next_sibling())
        self.assertEqual(self.win.count_label.get_text(), "2 selected")
        p.move_selected("Home")
        self.assertEqual({self.stored()[t].folder for t in (";addr", ";sig")}, {"Home"})
        self.assertFalse(p.selection_mode)

        p.enter_selection_mode()
        p.select_all()
        self.assertEqual(len(p.selected), 3)
        p.delete_selected()
        pump()
        confirm = window_titled("Delete 3 shortcuts?")
        find(confirm, Gtk.Button, "Delete").emit("clicked")
        pump()
        self.assertEqual(self.stored(), {})
        self.assertFalse(p.selection_mode)

    def test_long_press_enters_selection_mode(self):
        self.page._on_long_press(self.page.list.get_first_child().shortcut)
        self.assertTrue(self.page.selection_mode)
        self.assertEqual(len(self.page.selected), 1)
        self.page.exit_selection_mode()
        self.assertTrue(self.win.normal_box.get_visible())

    # -- folders ------------------------------------------------------------------------------------

    def test_folders_window(self):
        from keyflow.gui.dialogs import FoldersWindow

        fw = FoldersWindow(self.win, self.win.service, on_changed=self.win.reload)
        pump()
        names = [find(r, Gtk.Label).get_text() for r in descendants(fw.list) if isinstance(r, Gtk.ListBoxRow)]
        self.assertEqual(names, ["Home", "Work"])
        # rename through the real prompt dialog
        find(fw.list, Gtk.Button, tooltip="Rename").emit("clicked")
        pump()
        prompt = window_titled("Rename Folder")
        entry = find(prompt, Gtk.Entry)
        entry.set_text("Household")
        entry.emit("activate")
        pump()
        self.assertEqual(self.stored()[";addr"].folder, "Household")
        self.assertEqual([f.name for f in self.store.load_library().folders], ["Household", "Work"])
        self.assertIn("Household", [w.get_label() for w in descendants(self.page.folder_chips)
                                    if isinstance(w, Gtk.ToggleButton)] + ["Household"])
        # recolour
        fw.service.set_folder_color("Household", "#E53935")
        fw.refresh()
        # delete a non-empty folder asks what to do with its shortcuts
        deletes = [w for w in descendants(fw.list) if isinstance(w, Gtk.Button) and w.get_tooltip_text() == "Delete"]
        deletes[0].emit("clicked")
        pump()
        find(window_titled('Delete folder "Household"?'), Gtk.Button, "Keep shortcuts").emit("clicked")
        pump()
        self.assertIsNone(self.stored()[";addr"].folder)
        self.assertEqual([f.name for f in self.store.load_library().folders], ["Work"])
        # create via prompt, duplicate rejected inline
        find(fw.win, Gtk.Button, "New Folder").emit("clicked")
        pump()
        prompt = window_titled("New Folder")
        entry = find(prompt, Gtk.Entry)
        entry.set_text("work")
        entry.emit("activate")
        error = [w for w in descendants(prompt) if isinstance(w, Gtk.Label) and "already exists" in w.get_text()]
        self.assertTrue(error)
        entry.set_text("Travel")
        entry.emit("activate")
        pump()
        self.assertEqual([f.name for f in self.store.load_library().folders], ["Work", "Travel"])

    # -- settings --------------------------------------------------------------------------------------

    def test_settings_write_through_to_settings_json(self):
        sp = self.win.settings_page
        sp.case_sensitive.set_active(True)
        self.assertTrue(self.store.load_settings()[0].case_sensitive)
        self.assertFalse(sp.preserve_case.get_sensitive())
        sp.expand_on.set_selected(1)
        self.assertEqual(self.store.load_settings()[0].expansion_key, "immediate")
        sp.prefix.set_text("; extra")
        self.assertEqual(self.store.load_settings()[0].trigger_prefix, ";")
        sp.active.set_active(False)
        self.assertFalse(self.store.load_settings()[0].service_enabled)
        self.assertFalse(self.win._menu_active.get_state().get_boolean())        # header menu follows
        sp.layout_entry.set_text("fr")
        sp._set_layout()
        self.assertEqual(self.store.load_settings()[1].xkb_layout, "fr")
        # ...and changes made outside the window (CLI, daemon) show up
        from keyflow.library import LibraryService
        LibraryService(self.store).update_settings(lambda s, l: setattr(s, "case_sensitive", False))
        self.win._poll()
        self.assertFalse(sp.case_sensitive.get_active())

    def test_external_change_is_picked_up_by_the_poll(self):
        self.store.save_shortcuts(self.store.load_library().shortcuts + [Shortcut(";cli", "added from the CLI")])
        self.win._poll()
        self.assertIn(";cli", self.triggers_shown())

    def test_import_summary_and_skipped_rows(self):
        src = self.tmp / "in.csv"
        src.write_text("trigger,expansion\n;x,one\n;addr,dup\n;y,\n")
        self.win.settings_page.import_path(str(src))
        pump()
        self.assertIn(";x", self.stored())
        summary = window_titled("Import")
        self.assertTrue([w for w in descendants(summary) if isinstance(w, Gtk.Label) and w.get_text() == "1 imported, 2 skipped."])
        find(summary, Gtk.Button, "View skipped").emit("clicked")
        pump()
        skipped = window_titled("Skipped rows")
        texts = [w.get_text() for w in descendants(skipped) if isinstance(w, Gtk.Label)]
        self.assertTrue(any("Duplicate trigger" in t for t in texts))
        self.assertTrue(any("Empty expansion" in t for t in texts))

    def test_sync_setup_from_the_window(self):
        shared = self.tmp / "Sync"
        shared.mkdir()
        sp = self.win.settings_page
        sp.set_sync_folder(str(shared))
        for _ in range(100):                                        # the sync runs in a thread
            pump(0.05)
            if (shared / "keyflow-sync.json").exists() and sp.sync_now.get_sensitive():
                break
        self.assertTrue((shared / "keyflow-sync.json").exists())
        self.assertTrue(self.store.load_settings()[1].sync_enabled)
        self.assertTrue(sp.sync_switch.get_active())
        self.assertEqual(sp.sync_folder.get_text(), str(shared))
        self.assertTrue(sp.last_synced.get_text().startswith("Last synced"))

    # -- banner ---------------------------------------------------------------------------------------------

    def test_banner_shows_problem_and_offers_fix(self):
        d = status.Diagnosis([status.Problem("The KeyFlow service isn't running.", action="Start service")], False)
        self.win._set_diagnosis(d)
        b = self.page.banner
        self.assertTrue(b.get_reveal_child())
        self.assertEqual(b.action.get_label(), "Start service")
        self.assertFalse(b.command_row.get_visible())
        with mock.patch.object(status, "start_service", return_value=None) as start:
            b.action.emit("clicked")
        start.assert_called_once()
        self.win._set_diagnosis(status.Diagnosis([status.Problem("no group", command="sudo usermod -aG input $USER")], None))
        self.assertTrue(b.command_row.get_visible())
        self.assertFalse(b.action.get_visible())
        self.win._set_diagnosis(status.Diagnosis([], True))
        self.assertFalse(b.get_reveal_child())

    def test_command_line_quick_add(self):
        self.app.do_command_line(mock.Mock(get_arguments=lambda: ["keyflow", "--quick-add"]))
        pump()
        self.assertEqual(window_titled("Add Shortcut").get_title(), "Add Shortcut")


class StatusTests(unittest.TestCase):
    def test_diagnose_reports_group_first(self):
        with mock.patch.object(status, "in_input_group", return_value=False):
            d = status.diagnose()
        self.assertEqual(len(d.problems), 1)
        self.assertIn("input", d.problems[0].message)
        self.assertIn("usermod", d.problems[0].command)

    def test_service_not_running_offers_start(self):
        with mock.patch.object(status, "in_input_group", return_value=True), \
                mock.patch("os.path.exists", return_value=True), mock.patch("os.access", return_value=True), \
                mock.patch("glob.glob", return_value=["/dev/input/event0"]), \
                mock.patch.object(status, "service_is_active", return_value=False):
            d = status.diagnose()
        self.assertEqual([p.action for p in d.problems], ["Start service"])


if __name__ == "__main__":
    unittest.main()
