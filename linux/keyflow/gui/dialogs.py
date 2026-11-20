"""Modal windows: add/edit shortcut, quick add, folders, import summary, sync explainer."""

from __future__ import annotations

from typing import Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from .. import importers  # noqa: E402
from ..core import FOLDER_COLOR_PALETTE, Shortcut  # noqa: E402
from ..library import ImportOutcome, LibraryService  # noqa: E402
from . import util  # noqa: E402
from .util import button_row, confirm, label, make_window, padded_box, prompt_text  # noqa: E402

UNCATEGORIZED = "Uncategorized"
NEW_FOLDER = "New folder…"


def _editor() -> tuple[Gtk.ScrolledWindow, Gtk.TextView]:
    view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=6, bottom_margin=6,
                        left_margin=8, right_margin=8)
    scroller = Gtk.ScrolledWindow(min_content_height=140, hexpand=True, vexpand=True)
    scroller.set_child(view)
    scroller.add_css_class("kf-editor")
    return scroller, view


def _text(view: Gtk.TextView) -> str:
    buf = view.get_buffer()
    return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)


def _caption(text: str) -> Gtk.Label:
    return label(text, dim=True)


class ShortcutDialog:
    """Add (``editing`` None) or edit a shortcut — the counterpart of AddEditShortcutView."""

    def __init__(self, parent: Gtk.Window, service: LibraryService, editing: Optional[Shortcut] = None,
                 prefill: str = "", on_saved: Callable[[], None] = lambda: None) -> None:
        self.service, self.editing, self.on_saved = service, editing, on_saved
        self.win = make_window(parent, "Edit Shortcut" if editing else "Add Shortcut", 420)
        box = padded_box(14)
        title = label(self.win.get_title(), bold=True)
        title.add_css_class("title-3")
        box.append(title)

        box.append(_caption("Trigger"))
        self.trigger = Gtk.Entry(placeholder_text=";addr", text=editing.trigger if editing else "")
        box.append(self.trigger)

        box.append(_caption("Expansion — use {date}, {time}, {cursor} as placeholders"))
        scroller, self.expansion = _editor()
        self.expansion.get_buffer().set_text(editing.expansion if editing else prefill)
        box.append(scroller)

        box.append(_caption("Group (optional)"))
        self.group = Gtk.Entry(text=(editing.group or "") if editing else "")
        box.append(self.group)

        box.append(_caption("Folder"))
        self.folder_names: list[str] = []
        self.folder = Gtk.DropDown()
        self._folder_handler = self.folder.connect("notify::selected", self._on_folder_chosen)
        self._rebuild_folders(editing.folder if editing else None)
        box.append(self.folder)

        enabled_row = Gtk.Box(spacing=8)
        enabled_row.append(label("Enabled", wrap=False))
        enabled_row.append(Gtk.Box(hexpand=True))
        self.enabled = Gtk.Switch(active=editing.enabled if editing else True, valign=Gtk.Align.CENTER)
        enabled_row.append(self.enabled)
        box.append(enabled_row)

        self.error = label("")
        self.error.add_css_class("error")
        box.append(self.error)

        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        save.connect("clicked", lambda _b: self.save())
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _b: self.win.close())
        delete = None
        if editing:
            delete = Gtk.Button(label="Delete")
            delete.add_css_class("destructive-action")
            delete.connect("clicked", lambda _b: self.delete())
        box.append(button_row(cancel, save, start=delete))

        self.trigger.connect("activate", lambda _e: self.expansion.grab_focus())
        self.win.set_child(box)
        self.win.set_default_widget(save)
        self.win.present()
        self.trigger.grab_focus()

    # folders -----------------------------------------------------------------------------

    def _rebuild_folders(self, select: Optional[str]) -> None:
        self.folder_names = [f.name for f in self.service.load().folders]
        if select and select.lower() not in (n.lower() for n in self.folder_names):
            self.folder_names.append(select)
        entries = [UNCATEGORIZED] + self.folder_names + [NEW_FOLDER]
        index = 0
        if select:
            index = 1 + next(i for i, n in enumerate(self.folder_names) if n.lower() == select.lower())
        self.folder.handler_block(self._folder_handler)
        self.folder.set_model(Gtk.StringList.new(entries))
        self.folder.set_selected(index)
        self.folder.handler_unblock(self._folder_handler)
        self._last_folder_index = index

    def _on_folder_chosen(self, *_a) -> None:
        index = self.folder.get_selected()
        if index == len(self.folder_names) + 1:  # "New folder…"
            previous = self._last_folder_index
            self.folder.handler_block(self._folder_handler)
            self.folder.set_selected(previous)
            self.folder.handler_unblock(self._folder_handler)

            def create(name: str) -> Optional[str]:
                name = name.strip()
                if not name:
                    return "Folder name can't be empty"
                existing = next((n for n in self.folder_names if n.lower() == name.lower()), None)
                if existing is None:
                    err = self.service.create_folder(name)
                    if err:
                        return err
                self._rebuild_folders(existing or name)
                return None

            prompt_text(self.win, "New Folder", "Folder name", "", "Create", create)
        else:
            self._last_folder_index = index

    def selected_folder(self) -> Optional[str]:
        index = self.folder.get_selected()
        return self.folder_names[index - 1] if 1 <= index <= len(self.folder_names) else None

    # actions -----------------------------------------------------------------------------

    def save(self) -> None:
        edited = Shortcut(
            self.trigger.get_text(), _text(self.expansion), self.editing.is_rich_text if self.editing else False,
            self.group.get_text(), self.enabled.get_active(), self.selected_folder(),
        )
        error = self.service.save_shortcut(self.editing, edited)
        if error:
            self.error.set_text(error)
            return
        self.win.close()
        self.on_saved()

    def delete(self) -> None:
        assert self.editing is not None
        self.service.delete_shortcuts([self.editing])
        self.win.close()
        self.on_saved()


class QuickAddWindow:
    """Small, fast add form (the counterpart of QuickAddView)."""

    def __init__(self, parent: Optional[Gtk.Window], service: LibraryService, prefill: str = "",
                 on_saved: Callable[[], None] = lambda: None) -> None:
        self.service, self.on_saved = service, on_saved
        self.win = make_window(None, "Add Shortcut", 340)
        if parent is not None:
            self.win.set_transient_for(parent)
        box = padded_box(12, 16)
        box.append(label("Add Shortcut", bold=True))
        self.trigger = Gtk.Entry(placeholder_text="Trigger, e.g. ;addr")
        box.append(self.trigger)
        scroller, self.expansion = _editor()
        scroller.set_min_content_height(100)
        self.expansion.get_buffer().set_text(prefill)
        box.append(scroller)
        self.error = label("")
        self.error.add_css_class("error")
        box.append(self.error)
        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        save.connect("clicked", lambda _b: self.save())
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _b: self.win.close())
        box.append(button_row(cancel, save))
        self.trigger.connect("activate", lambda _e: self.expansion.grab_focus())
        self.win.set_child(box)
        self.win.set_default_widget(save)
        self.win.present()
        (self.expansion if prefill else self.trigger).grab_focus()

    def save(self) -> None:
        trigger, expansion = self.trigger.get_text().strip(" \t"), _text(self.expansion)
        if not trigger or not expansion:
            self.error.set_text("Both fields are required")
            return
        error = self.service.save_shortcut(None, Shortcut(trigger, expansion))
        if error:
            self.error.set_text(f'"{trigger}" is already used' if "already used" in error else error)
            return
        self.win.close()
        self.on_saved()


class FoldersWindow:
    """Create, rename, recolour and delete folders (the counterpart of FoldersView)."""

    def __init__(self, parent: Gtk.Window, service: LibraryService, on_changed: Callable[[], None]) -> None:
        self.service, self.on_changed = service, on_changed
        self.win = make_window(parent, "Folders", 420, 460)
        outer = padded_box(12, 16)
        header = Gtk.Box(spacing=8)
        header.append(label("Folders", bold=True))
        header.append(Gtk.Box(hexpand=True))
        new = Gtk.Button(label="New Folder")
        new.connect("clicked", lambda _b: self._new())
        header.append(new)
        outer.append(header)
        self.error = label("")
        self.error.add_css_class("error")
        outer.append(self.error)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list.add_css_class("kf-card")
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_child(self.list)
        outer.append(scroller)
        close = Gtk.Button(label="Close")
        close.connect("clicked", lambda _b: self.win.close())
        outer.append(button_row(close))
        self.win.set_child(outer)
        self.refresh()
        self.win.present()

    def _changed(self, error: Optional[str]) -> None:
        self.error.set_text(error or "")
        self.refresh()
        self.on_changed()

    def refresh(self) -> None:
        while (child := self.list.get_first_child()) is not None:
            self.list.remove(child)
        lib = self.service.load()
        counts: dict[str, int] = {}
        for s in lib.shortcuts:
            if s.folder:
                counts[s.folder.lower()] = counts.get(s.folder.lower(), 0) + 1
        if not lib.folders:
            self.list.append(Gtk.ListBoxRow(child=label("No folders yet.", dim=True), selectable=False,
                                            activatable=False))
        for folder in lib.folders:
            row = Gtk.Box(spacing=10)
            row.append(self._color_button(folder.name, folder.color_tag))
            texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
            name = label(folder.name, wrap=False)
            name.add_css_class("kf-trigger")
            n = counts.get(folder.name.lower(), 0)
            texts.append(name)
            texts.append(label(f"{n} shortcut{'' if n == 1 else 's'}", dim=True))
            row.append(texts)
            rename = Gtk.Button(icon_name="document-edit-symbolic", tooltip_text="Rename", valign=Gtk.Align.CENTER)
            rename.add_css_class("flat")
            rename.connect("clicked", lambda _b, f=folder.name: self._rename(f))
            delete = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Delete", valign=Gtk.Align.CENTER)
            delete.add_css_class("flat")
            delete.connect("clicked", lambda _b, f=folder.name, c=n: self._delete(f, c))
            row.append(rename)
            row.append(delete)
            self.list.append(Gtk.ListBoxRow(child=row, selectable=False, activatable=False))

    def _color_button(self, name: str, current: Optional[str]) -> Gtk.MenuButton:
        button = Gtk.MenuButton(valign=Gtk.Align.CENTER, tooltip_text="Colour")
        button.add_css_class("flat")
        button.set_child(util.swatch(current))
        pop = Gtk.Popover()
        grid = Gtk.Grid(row_spacing=6, column_spacing=6)
        for margin in ("start", "end", "top", "bottom"):
            getattr(grid, f"set_margin_{margin}")(8)
        choices = [None] + FOLDER_COLOR_PALETTE
        for i, color in enumerate(choices):
            b = Gtk.Button(tooltip_text=color or "No colour")
            b.add_css_class("flat")
            b.set_child(util.swatch(color))
            b.connect("clicked", lambda _b, c=color: (pop.popdown(), self._changed(self.service.set_folder_color(name, c))))
            grid.attach(b, i % 5, i // 5, 1, 1)
        pop.set_child(grid)
        button.set_popover(pop)
        return button

    def _new(self) -> None:
        prompt_text(self.win, "New Folder", "Folder name", "", "Create",
                    lambda text: self._after(self.service.create_folder(text)))

    def _rename(self, old: str) -> None:
        prompt_text(self.win, "Rename Folder", "Folder name", old, "Rename",
                    lambda text: self._after(self.service.rename_folder(old, text)))

    def _after(self, error: Optional[str]) -> Optional[str]:
        if error is None:
            self._changed(None)
        return error

    def _delete(self, name: str, count: int) -> None:
        if count == 0:
            self._changed(self.service.delete_folder(name, delete_shortcuts=False))
            return
        confirm(
            self.win, f'Delete folder "{name}"?',
            f"It contains {count} shortcut{'' if count == 1 else 's'}.",
            [("Keep shortcuts", None, lambda: self._changed(self.service.delete_folder(name, False))),
             ("Delete shortcuts too", "destructive", lambda: self._changed(self.service.delete_folder(name, True)))],
        )


class SkippedRowsWindow:
    """Rows an import couldn't use, with a CSV export so they can be fixed and re-imported."""

    def __init__(self, parent: Gtk.Window, skipped: list[importers.SkippedRow]) -> None:
        self.skipped = skipped
        self.win = make_window(parent, "Skipped rows", 480, 460)
        box = padded_box(12, 16)
        box.append(label(f"{len(skipped)} row{'' if len(skipped) == 1 else 's'} skipped", bold=True))
        lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        lb.add_css_class("kf-card")
        for row in skipped:
            cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            head = f"Row {row.row_number}: {row.reason}" if row.row_number else row.reason
            cell.append(label(head, wrap=False))
            detail = row.trigger or row.raw_row
            if detail:
                cell.append(label(util.one_line(detail), dim=True, wrap=False, mono=True))
            lb.append(Gtk.ListBoxRow(child=cell, selectable=False, activatable=False))
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_child(lb)
        box.append(scroller)
        self.status = label("", dim=True)
        box.append(self.status)
        export = Gtk.Button(label="Export CSV")
        export.connect("clicked", lambda _b: self.export())
        close = Gtk.Button(label="Close")
        close.add_css_class("suggested-action")
        close.connect("clicked", lambda _b: self.win.close())
        box.append(button_row(export, close))
        self.win.set_child(box)
        self.win.present()

    def export(self) -> None:
        def write(path: str) -> None:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(importers.skipped_to_csv(self.skipped))
                self.status.set_text(f"Exported to {path}")
            except OSError as e:
                self.status.set_text(f"Export failed: {e}")

        util.FilePicker.save(self.win, "Export skipped rows", "keyflow_skipped_rows.csv",
                             [("CSV", ["*.csv"])], write)


def show_import_summary(parent: Gtk.Window, outcome: ImportOutcome) -> Gtk.Window:
    n = len(outcome.skipped)
    title = f"{outcome.imported} imported" + ("." if not n else f", {n} skipped.")
    win = make_window(parent, "Import", 340)
    box = padded_box()
    box.append(label(title, bold=True))
    ok = Gtk.Button(label="OK")
    ok.connect("clicked", lambda _b: win.close())
    buttons: list[Gtk.Widget] = []
    if n:
        view = Gtk.Button(label="View skipped")
        view.connect("clicked", lambda _b: (win.close(), SkippedRowsWindow(parent, outcome.skipped)))
        buttons.append(view)
    ok.add_css_class("suggested-action")
    buttons.append(ok)
    box.append(button_row(*buttons))
    win.set_child(box)
    win.set_default_widget(ok)
    win.present()
    return win


EXPLAINER_POINTS = [
    "Pick the same synced folder here that you use on your other device.",
    "Changes you make locally are written to the file a couple of seconds after you make them, "
    "while the KeyFlow service is running.",
    "Changes that arrive from another device are picked up automatically and merged in — newer "
    "edits win, and deletions carry over too.",
    "If nothing appears, check that Syncthing (or whatever you're using) actually has that "
    "folder syncing and connected.",
]


def show_sync_explainer(parent: Gtk.Window, on_dismiss: Callable[[], None] = lambda: None) -> Gtk.Window:
    win = make_window(parent, "Syncing", 440)
    box = padded_box(14)
    title = label("Syncing shortcuts between devices", bold=True)
    title.add_css_class("title-3")
    box.append(title)
    box.append(label(
        "KeyFlow doesn't sync through a server — it just reads and writes one file, "
        "keyflow-sync.json, in a folder you choose. Point that folder at one that Syncthing (or "
        "Dropbox, a network share, anything that mirrors a folder) is already keeping in sync with "
        "your other devices, and KeyFlow keeps that file up to date automatically."))
    for point in EXPLAINER_POINTS:
        row = Gtk.Box(spacing=8, valign=Gtk.Align.START)
        row.append(label("•", wrap=False))
        row.append(label(point))
        box.append(row)
    got_it = Gtk.Button(label="Got it")
    got_it.add_css_class("suggested-action")
    got_it.connect("clicked", lambda _b: (win.close(), on_dismiss()))
    box.append(button_row(got_it))
    win.set_child(box)
    win.set_default_widget(got_it)
    win.present()
    return win
