"""The Shortcuts tab (counterpart of ShortcutListView)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from ..core import Shortcut  # noqa: E402
from ..library import ALL, UNCATEGORIZED, filter_shortcuts  # noqa: E402
from . import util  # noqa: E402
from .banner import Banner  # noqa: E402
from .dialogs import ShortcutDialog  # noqa: E402
from .util import confirm, label, shortcut_key  # noqa: E402

if TYPE_CHECKING:
    from .window import MainWindow

class ShortcutsPage(Gtk.Box):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win = win
        self.query = ""
        self.group: Optional[str] = None
        self.folder_filter = ALL
        self.scoped = False
        self.selected: set[str] = set()
        self.selection_mode = False
        self._suppress_activate = False
        self._rebuilding = False

        self.banner = Banner(win.on_banner_action)
        self.append(self.banner)

        self.search = Gtk.SearchEntry(placeholder_text="Search shortcuts", hexpand=True)
        for side in ("start", "end", "top", "bottom"):
            getattr(self.search, f"set_margin_{side}")(12 if side in ("start", "end") else 8)
        self.search.connect("search-changed", self._on_search)
        self.append(self.search)

        self.folder_chips = Gtk.Box(spacing=6)
        self.append(self._scroller(self.folder_chips))
        self.scope_check = Gtk.CheckButton(label="Search this folder only")
        self.scope_check.set_margin_start(16)
        self.scope_check.set_margin_bottom(6)
        self.scope_check.connect("toggled", self._on_scope)
        self.scope_check.set_visible(False)
        self.append(self.scope_check)
        self.group_chips = Gtk.Box(spacing=6)
        self.group_scroller = self._scroller(self.group_chips)
        self.append(self.group_scroller)

        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, show_separators=True)
        self.list.connect("row-activated", self._on_row_activated)
        self.list_scroller = Gtk.ScrolledWindow(vexpand=True)
        self.list_scroller.set_child(self.list)
        self.empty = label("", dim=True)
        self.empty.set_halign(Gtk.Align.CENTER)
        self.empty.set_valign(Gtk.Align.CENTER)
        self.stack = Gtk.Stack(vexpand=True)
        self.stack.add_named(self.list_scroller, "list")
        self.stack.add_named(self.empty, "empty")
        self.append(self.stack)

    @staticmethod
    def _scroller(child: Gtk.Widget) -> Gtk.ScrolledWindow:
        sw = Gtk.ScrolledWindow(vscrollbar_policy=Gtk.PolicyType.NEVER,
                                hscrollbar_policy=Gtk.PolicyType.AUTOMATIC, propagate_natural_height=True)
        sw.set_child(child)
        child.set_margin_start(12)
        child.set_margin_end(12)
        child.set_margin_bottom(8)
        return sw

    # -- header / signals ---------------------------------------------------------------------

    def _on_search(self, entry: Gtk.SearchEntry) -> None:
        self.query = entry.get_text()
        self.rebuild_list()

    def _on_scope(self, check: Gtk.CheckButton) -> None:
        self.scoped = check.get_active()
        self.rebuild_list()

    def _chip(self, title: str, active: bool, on_click: Callable[[], None], swatch_color: Optional[str] = None,
              with_swatch: bool = False) -> Gtk.ToggleButton:
        b = Gtk.ToggleButton(active=active)
        b.add_css_class("kf-chip")
        if with_swatch:
            inner = Gtk.Box(spacing=6)
            inner.append(util.swatch(swatch_color))
            inner.append(Gtk.Label(label=title))
            b.set_child(inner)
        else:
            b.set_label(title)
        b.connect("clicked", lambda _b: on_click())
        return b

    def _set_folder(self, value: str) -> None:
        self.folder_filter = value
        self.scope_check.set_visible(value != ALL)
        self.rebuild_chips()
        self.rebuild_list()

    def _set_group(self, value: Optional[str]) -> None:
        self.group = None if value == self.group else value
        self.rebuild_chips()
        self.rebuild_list()

    def rebuild_chips(self) -> None:
        for box in (self.folder_chips, self.group_chips):
            while (c := box.get_first_child()) is not None:
                box.remove(c)
        lib = self.win.library
        names = [f.name for f in lib.folders]
        if self.folder_filter not in (ALL, UNCATEGORIZED) and self.folder_filter.lower() not in (n.lower() for n in names):
            self.folder_filter = ALL           # the filtered folder was deleted elsewhere
            self.scope_check.set_visible(False)
        self.folder_chips.append(self._chip("All", self.folder_filter == ALL, lambda: self._set_folder(ALL)))
        self.folder_chips.append(self._chip("Uncategorized", self.folder_filter == UNCATEGORIZED,
                                            lambda: self._set_folder(UNCATEGORIZED)))
        for f in lib.folders:
            active = self.folder_filter.lower() == f.name.lower()
            self.folder_chips.append(self._chip(f.name, active, lambda n=f.name: self._set_folder(n),
                                                f.color_tag, with_swatch=bool(f.color_tag)))
        groups = sorted({s.group for s in lib.shortcuts if s.group}, key=str.lower)
        if self.group not in groups:
            self.group = None
        self.group_scroller.set_visible(bool(groups))
        if groups:
            self.group_chips.append(self._chip("All groups", self.group is None, lambda: self._set_group(None)))
            for g in groups:
                self.group_chips.append(self._chip(g, self.group == g, lambda g=g: self._set_group(g)))

    # -- list -----------------------------------------------------------------------------

    @property
    def visible_shortcuts(self) -> list[Shortcut]:
        return filter_shortcuts(self.win.library.shortcuts, self.query, self.group, self.folder_filter, self.scoped)

    def rebuild_list(self) -> None:
        self._rebuilding = True
        adj = self.list_scroller.get_vadjustment()
        position = adj.get_value()
        while (c := self.list.get_first_child()) is not None:
            self.list.remove(c)
        keys_now = {shortcut_key(s) for s in self.win.library.shortcuts}
        self.selected &= keys_now      # shortcuts deleted elsewhere drop out of the selection

        shown = self.visible_shortcuts
        for s in shown:
            self.list.append(self._make_row(s))
        if shown:
            self.stack.set_visible_child_name("list")
            GLib.idle_add(lambda: (adj.set_value(position), False)[1])
        else:
            self.empty.set_text("No shortcuts yet. Click + to add one." if not self.query else "No matches.")
            self.stack.set_visible_child_name("empty")
        self._rebuilding = False
        self.win.on_selection_changed()

    def _make_row(self, s: Shortcut) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow(activatable=True)
        row.shortcut = s  # type: ignore[attr-defined]
        box = Gtk.Box(spacing=10)
        for side in ("start", "end"):
            getattr(box, f"set_margin_{side}")(12)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        selected = shortcut_key(s) in self.selected
        if self.selection_mode:
            check = Gtk.CheckButton(active=selected, can_target=False, can_focus=False, valign=Gtk.Align.CENTER)
            box.append(check)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        trig = Gtk.Label(label=s.trigger, xalign=0)
        trig.add_css_class("kf-trigger")
        texts.append(trig)
        preview = Gtk.Label(label=util.one_line(s.expansion), xalign=0, ellipsize=Pango.EllipsizeMode.END)
        preview.add_css_class("dim-label")
        texts.append(preview)
        box.append(texts)
        if not self.selection_mode:
            sw = Gtk.Switch(active=s.enabled, valign=Gtk.Align.CENTER)
            sw.connect("notify::active", lambda w, _p, s=s: self._on_toggle(s, w.get_active()))
            box.append(sw)
        row.set_child(box)
        if selected:
            row.add_css_class("kf-row-selected")

        long_press = Gtk.GestureLongPress()
        long_press.connect("pressed", lambda g, _x, _y, s=s: self._on_long_press(s))
        row.add_controller(long_press)
        click = Gtk.GestureClick(button=3)
        click.connect("pressed", lambda g, _n, x, y, row=row, s=s: self._context_menu(row, s, x, y))
        row.add_controller(click)
        return row

    def _on_toggle(self, s: Shortcut, enabled: bool) -> None:
        if self._rebuilding:
            return
        error = self.win.service.set_enabled(s, enabled)
        self.win.reload(error)

    def _on_row_activated(self, _lb: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        if self._suppress_activate:
            return
        s: Shortcut = row.shortcut  # type: ignore[attr-defined]
        if self.selection_mode:
            self.toggle_selected(s)
        else:
            ShortcutDialog(self.win, self.win.service, editing=s, on_saved=self.win.reload)

    def _on_long_press(self, s: Shortcut) -> None:
        if not self.selection_mode:
            self.selection_mode = True
            self.selected = {shortcut_key(s)}
            self._suppress_activate = True
            GLib.timeout_add(400, lambda: (setattr(self, "_suppress_activate", False), False)[1])
            self.win.on_selection_mode_changed()
            self.rebuild_list()

    def enter_selection_mode(self) -> None:
        if not self.selection_mode and self.visible_shortcuts:
            self.selection_mode = True
            self.win.on_selection_mode_changed()
            self.rebuild_list()

    def toggle_selected(self, s: Shortcut) -> None:
        key = shortcut_key(s)
        self.selected.symmetric_difference_update({key})
        self.rebuild_list()

    def select_all(self) -> None:
        self.selected = {shortcut_key(s) for s in self.visible_shortcuts}
        self.rebuild_list()

    def exit_selection_mode(self) -> None:
        self.selection_mode = False
        self.selected = set()
        self.win.on_selection_mode_changed()
        self.rebuild_list()

    def selected_shortcuts(self) -> list[Shortcut]:
        return [s for s in self.win.library.shortcuts if shortcut_key(s) in self.selected]

    def _context_menu(self, row: Gtk.ListBoxRow, s: Shortcut, x: float, y: float) -> None:
        if self.selection_mode:
            return
        pop = Gtk.Popover()
        pop.set_parent(row)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for text, cb in (("Edit…", lambda: ShortcutDialog(self.win, self.win.service, editing=s, on_saved=self.win.reload)),
                         ("Delete", lambda: self.win.reload(self.win.service.delete_shortcuts([s])))):
            b = Gtk.Button(label=text, halign=Gtk.Align.FILL)
            b.add_css_class("flat")
            b.connect("clicked", lambda _b, cb=cb: (pop.popdown(), cb()))
            box.append(b)
        pop.set_child(box)
        pop.connect("closed", lambda p: GLib.idle_add(lambda: (p.unparent(), False)[1]))
        pop.popup()

    # -- bulk actions ---------------------------------------------------------------------------

    def move_selected(self, folder: Optional[str]) -> None:
        error = self.win.service.move_to_folder(self.selected_shortcuts(), folder)
        self.exit_selection_mode()
        self.win.reload(error)

    def delete_selected(self) -> None:
        n = len(self.selected)
        confirm(self.win, f"Delete {n} shortcut{'' if n == 1 else 's'}?", "This can't be undone.",
                [("Delete", "destructive", self._do_delete_selected)])

    def _do_delete_selected(self) -> None:
        error = self.win.service.delete_shortcuts(self.selected_shortcuts())
        self.exit_selection_mode()
        self.win.reload(error)
