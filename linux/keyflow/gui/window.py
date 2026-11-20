"""The main window: Shortcuts / Settings tabs (counterpart of RootView + the menu bar menu)."""

from __future__ import annotations

import threading
from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from .. import status  # noqa: E402
from ..core import AppSettings  # noqa: E402
from ..library import LibraryService  # noqa: E402
from ..store import Library, LinuxConfig, Store  # noqa: E402
from .dialogs import FoldersWindow, QuickAddWindow, ShortcutDialog  # noqa: E402
from .settings_page import SettingsPage  # noqa: E402
from .shortcuts_page import ShortcutsPage  # noqa: E402
from .util import label  # noqa: E402

POLL_MS = 1000
DIAGNOSE_EVERY = 5   # polls


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application, store: Store) -> None:
        super().__init__(application=app, title="KeyFlow", default_width=560, default_height=640)
        self.store = store
        self.service = LibraryService(store)
        self.library = Library()
        self.settings = AppSettings()
        self.linux = LinuxConfig()
        self.diagnosis: Optional[status.Diagnosis] = None
        self._fingerprint = None
        self._ticks = 0
        self.set_icon_name("keyflow")

        self.shortcuts_page = ShortcutsPage(self)
        self.settings_page = SettingsPage(self)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_titled(self.shortcuts_page, "shortcuts", "Shortcuts")
        self.stack.add_titled(self.settings_page, "settings", "Settings")
        self.stack.connect("notify::visible-child", lambda *_: self.on_selection_mode_changed())
        self.set_child(self.stack)

        self._build_header()
        self._build_actions()
        self.reload()
        self.refresh_diagnosis()
        GLib.timeout_add(POLL_MS, self._poll)

    # -- header bar ------------------------------------------------------------------------------

    def _build_header(self) -> None:
        header = Gtk.HeaderBar()
        self.switcher = Gtk.StackSwitcher(stack=self.stack)
        self.count_label = label("", wrap=False)
        self.count_label.set_visible(False)
        title = Gtk.Box(spacing=0)
        title.append(self.switcher)
        title.append(self.count_label)
        header.set_title_widget(title)

        def icon_button(icon: str, tip: str, cb) -> Gtk.Button:
            b = Gtk.Button(icon_name=icon, tooltip_text=tip)
            b.connect("clicked", lambda _b: cb())
            return b

        # normal mode
        self.add_button = icon_button("list-add-symbolic", "Add Shortcut", self.add_shortcut)
        self.folders_button = icon_button("folder-symbolic", "Folders", self.open_folders)
        self.select_button = icon_button("object-select-symbolic", "Select shortcuts", lambda: self.shortcuts_page.enter_selection_mode())
        self.normal_box = Gtk.Box(spacing=6)
        for b in (self.add_button, self.folders_button, self.select_button):
            self.normal_box.append(b)
        header.pack_start(self.normal_box)

        # selection mode
        self.cancel_button = icon_button("window-close-symbolic", "Cancel selection", lambda: self.shortcuts_page.exit_selection_mode())
        self.select_all_button = icon_button("edit-select-all-symbolic", "Select all", lambda: self.shortcuts_page.select_all())
        self.move_button = Gtk.MenuButton(icon_name="folder-symbolic", tooltip_text="Move to folder")
        self.move_popover = Gtk.Popover()
        self.move_button.set_popover(self.move_popover)
        self.move_button.connect("notify::active", lambda *_: self._fill_move_menu())
        self.delete_button = icon_button("user-trash-symbolic", "Delete selected", lambda: self.shortcuts_page.delete_selected())
        self.delete_button.add_css_class("destructive-action")
        self.selection_box = Gtk.Box(spacing=6)
        for b in (self.cancel_button, self.select_all_button, self.move_button, self.delete_button):
            self.selection_box.append(b)
        header.pack_start(self.selection_box)
        self.selection_box.set_visible(False)

        menu = Gio.Menu()
        menu.append("Expansion active", "win.expansion-active")
        menu.append("Add Shortcut…", "win.quick-add")
        menu.append("Sync now", "win.sync-now")
        menu.append("Quit KeyFlow", "app.quit")
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Main menu")
        header.pack_end(menu_button)
        self.set_titlebar(header)

    def _build_actions(self) -> None:
        active = Gio.SimpleAction.new_stateful("expansion-active", None, GLib.Variant.new_boolean(True))
        active.connect("change-state", self._on_menu_expansion)
        self.add_action(active)
        self._menu_active = active
        for name, cb in (("quick-add", lambda *_: self.quick_add()),
                         ("sync-now", lambda *_: self.settings_page.run_sync())):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", cb)
            self.add_action(act)

    def _on_menu_expansion(self, action: Gio.SimpleAction, value: GLib.Variant) -> None:
        enabled = value.get_boolean()
        action.set_state(value)
        error = self.service.update_settings(lambda s, l: setattr(s, "service_enabled", enabled))
        self.reload(error)

    def _fill_move_menu(self) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        entries = [("Uncategorized", None)] + [(f.name, f.name) for f in self.library.folders]
        for text, value in entries:
            b = Gtk.Button(label=text)
            b.add_css_class("flat")
            b.connect("clicked", lambda _b, v=value: (self.move_popover.popdown(), self.shortcuts_page.move_selected(v)))
            box.append(b)
        self.move_popover.set_child(box)

    def on_selection_mode_changed(self) -> None:
        selecting = self.shortcuts_page.selection_mode and self.stack.get_visible_child_name() == "shortcuts"
        self.selection_box.set_visible(selecting)
        self.normal_box.set_visible(not selecting)
        self.switcher.set_visible(not selecting)
        self.count_label.set_visible(selecting)

    def on_selection_changed(self) -> None:
        n = len(self.shortcuts_page.selected)
        self.count_label.set_text(f"{n} selected")
        self.on_selection_mode_changed()

    # -- data ---------------------------------------------------------------------------------------

    def reload(self, error: Optional[str] = None) -> None:
        """Re-read everything from disk and refresh both tabs."""
        try:
            self.library = self.store.load_library()
            self.settings, self.linux = self.store.load_settings()
        except (OSError, ValueError) as e:
            error = error or str(e)
        self._fingerprint = self._current_fingerprint()
        self._menu_active.set_state(GLib.Variant.new_boolean(self.settings.service_enabled))
        self.shortcuts_page.rebuild_chips()
        self.shortcuts_page.rebuild_list()
        self.settings_page.refresh()
        self.shortcuts_page.banner.update(self.diagnosis)
        if error:
            self.toast(error)

    def _current_fingerprint(self):
        return (self.store.fingerprint(), self.store.sync_state_path.stat().st_mtime_ns
                if self.store.sync_state_path.exists() else None)

    def _poll(self) -> bool:
        if self._current_fingerprint() != self._fingerprint:
            self.reload()      # the CLI, the sync, or another window changed something
        self._ticks += 1
        if self._ticks % DIAGNOSE_EVERY == 0:
            self.refresh_diagnosis()
        return True

    def refresh_diagnosis(self) -> None:
        def work() -> None:
            d = status.diagnose()
            GLib.idle_add(lambda: (self._set_diagnosis(d), False)[1])

        threading.Thread(target=work, daemon=True).start()

    def _set_diagnosis(self, d: status.Diagnosis) -> None:
        self.diagnosis = d
        self.shortcuts_page.banner.update(d)
        self.settings_page.refresh()

    def on_banner_action(self) -> None:
        error = status.start_service()
        self.toast(error or "Service started")
        self.refresh_diagnosis()

    # -- actions ----------------------------------------------------------------------------------------

    def add_shortcut(self, prefill: str = "") -> None:
        ShortcutDialog(self, self.service, prefill=prefill, on_saved=self.reload)

    def quick_add(self, prefill: str = "") -> None:
        QuickAddWindow(self, self.service, prefill, on_saved=self.reload)

    def open_folders(self) -> None:
        FoldersWindow(self, self.service, on_changed=self.reload)

    def toast(self, message: str) -> None:
        """A short, non-blocking message at the bottom of the shortcuts tab."""
        self.last_toast = message
        self.shortcuts_page.empty.set_tooltip_text(message)
        bar = getattr(self, "_toast_label", None)
        if bar is None:
            bar = label("", wrap=True)
            bar.add_css_class("kf-banner")
            bar.set_margin_start(16)
            bar.set_margin_end(16)
            bar.set_margin_bottom(8)
            self._toast_label = bar
            self.shortcuts_page.append(bar)
        bar.set_text(message)
        bar.set_visible(True)
        GLib.timeout_add_seconds(5, lambda: (bar.set_visible(False), False)[1])
