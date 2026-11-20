"""The Settings tab (counterpart of SettingsView)."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from ..core import EXPANSION_BOUNDARY, EXPANSION_IMMEDIATE  # noqa: E402
from ..layout import detect_layout  # noqa: E402
from ..sync import SYNC_FILE_NAME, SyncEngine, SyncState  # noqa: E402
from . import util  # noqa: E402
from .banner import Banner  # noqa: E402
from .dialogs import show_import_summary, show_sync_explainer  # noqa: E402
from .util import label  # noqa: E402

if TYPE_CHECKING:
    from .window import MainWindow


class SettingsPage(Gtk.ScrolledWindow):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__(vexpand=True)
        self.win = win
        self._loading = True
        self._file_choosers_enabled = True
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        for side in ("start", "end", "top", "bottom"):
            getattr(root, f"set_margin_{side}")(18)
        self.set_child(root)
        clamp = root

        # -- Expansion ---------------------------------------------------------------------
        self.banner = Banner(win.on_banner_action)
        self.banner.set_margin_start(0)
        self.banner.set_margin_end(0)
        self.banner.set_margin_top(0)
        clamp.append(self.banner)
        card = self._section(clamp, "Expansion")
        self.active = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.active.connect("notify::active", lambda w, _p: self._set(lambda s, l: setattr(s, "service_enabled", w.get_active())))
        self._row(card, "Expansion active", self.active,
                  "Pause to temporarily stop all expansion without stopping the service.")
        self.service_status = label("", dim=True, wrap=False)
        self._row(card, "Service", self.service_status)

        # -- Matching ----------------------------------------------------------------------
        card = self._section(clamp, "Matching")
        self.case_sensitive = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.case_sensitive.connect("notify::active", self._on_case_sensitive)
        self._row(card, "Case sensitive", self.case_sensitive)
        self.preserve_case = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.preserve_case.connect("notify::active", lambda w, _p: self._set(lambda s, l: setattr(s, "preserve_case", w.get_active())))
        self._row(card, "Preserve typed case", self.preserve_case,
                  "If you type ;ADDR in caps, the expansion is capitalized too.")
        self.expand_on = Gtk.DropDown.new_from_strings(["Space or punctuation", "Immediate match"])
        self.expand_on.connect("notify::selected", self._on_expand_on)
        self._row(card, "Expand on", self.expand_on)
        self.prefix = Gtk.Entry(max_length=1, width_chars=3, placeholder_text=";", valign=Gtk.Align.CENTER)
        self.prefix.connect("changed", lambda e: self._set(lambda s, l: setattr(s, "trigger_prefix", e.get_text()[:1])))
        self._row(card, "Required prefix character", self.prefix, "Optional, e.g. ;")

        # -- Keyboard ----------------------------------------------------------------------
        card = self._section(clamp, "Keyboard layout")
        self.layout_detected = label("", dim=True, wrap=False)
        self._row(card, "Detected", self.layout_detected,
                  "KeyFlow types expansions with this layout. Override it if it is wrong.")
        self.layout_entry = Gtk.Entry(placeholder_text="e.g. fr", width_chars=10, valign=Gtk.Align.CENTER)
        self.layout_entry.connect("activate", lambda e: self._set_layout())
        apply_btn = Gtk.Button(label="Apply", valign=Gtk.Align.CENTER)
        apply_btn.connect("clicked", lambda _b: self._set_layout())
        box = Gtk.Box(spacing=6)
        box.append(self.layout_entry)
        box.append(apply_btn)
        self._row(card, "Override layout", box)

        # -- Sync --------------------------------------------------------------------------
        card = self._section(clamp, "Sync")
        self.sync_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.sync_switch.connect("notify::active", self._on_sync_toggle)
        self._row(card, "Sync via Syncthing", self.sync_switch)
        self.sync_folder = label("No folder selected", dim=True, wrap=False)
        self.sync_folder.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.sync_folder.set_hexpand(True)
        self.choose_folder = Gtk.Button(label="Choose Folder…", valign=Gtk.Align.CENTER)
        self.choose_folder.connect("clicked", lambda _b: self.pick_sync_folder())
        self._row(card, "Folder", self.choose_folder, extra=self.sync_folder)
        self.sync_now = Gtk.Button(label="Sync Now", valign=Gtk.Align.CENTER)
        self.sync_now.connect("clicked", lambda _b: self.run_sync())
        self.last_synced = label("Never synced", dim=True)
        self._row(card, "Status", self.sync_now, extra=self.last_synced)
        self.sync_error = label("")
        self.sync_error.add_css_class("error")
        card.get_parent().append(self.sync_error)
        about = Gtk.Button(label="About Syncthing sync…", halign=Gtk.Align.START)
        about.add_css_class("flat")
        about.connect("clicked", lambda _b: show_sync_explainer(self.win, lambda: util.set_gui_flag("syncExplainerShown")))
        card.get_parent().append(about)

        # -- Import & export ---------------------------------------------------------------
        card = self._section(clamp, "Import & Export")
        export_json = Gtk.Button(label="Export JSON")
        export_json.connect("clicked", lambda _b: self.export("json"))
        export_csv = Gtk.Button(label="Export CSV")
        export_csv.connect("clicked", lambda _b: self.export("csv"))
        box = Gtk.Box(spacing=6)
        box.append(export_json)
        box.append(export_csv)
        self._row(card, "Export", box)
        self.overwrite = Gtk.Switch(valign=Gtk.Align.CENTER)
        self._row(card, "Overwrite existing shortcuts with matching triggers", self.overwrite)
        import_btn = Gtk.Button(label="Import JSON or CSV…", valign=Gtk.Align.CENTER)
        import_btn.connect("clicked", lambda _b: self.choose_import())
        self._row(card, "Import", import_btn)
        self.io_status = label("", dim=True)
        card.get_parent().append(self.io_status)
        self._loading = False

    # -- layout helpers -------------------------------------------------------------------------

    def _section(self, parent: Gtk.Box, title: str) -> Gtk.ListBox:
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        section.append(label(title, bold=True))
        card = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        card.add_css_class("kf-card")
        section.append(card)
        parent.append(section)
        return card

    def _row(self, card: Gtk.ListBox, title: str, control: Gtk.Widget, subtitle: str = "",
             extra: Optional[Gtk.Widget] = None) -> None:
        row = Gtk.Box(spacing=12)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        texts.append(label(title))
        if subtitle:
            sub = label(subtitle, dim=True)
            sub.add_css_class("caption")
            texts.append(sub)
        if extra is not None:
            texts.append(extra)
        row.append(texts)
        control.set_valign(Gtk.Align.CENTER)
        row.append(control)
        card.append(Gtk.ListBoxRow(child=row, selectable=False, activatable=False))

    # -- settings writes ---------------------------------------------------------------------------

    def _set(self, fn: Callable) -> None:
        if self._loading:
            return
        error = self.win.service.update_settings(fn)
        self.win.reload(error)

    def _on_case_sensitive(self, w: Gtk.Switch, _p) -> None:
        self.preserve_case.set_sensitive(not w.get_active())
        self._set(lambda s, l: setattr(s, "case_sensitive", w.get_active()))

    def _on_expand_on(self, w: Gtk.DropDown, _p) -> None:
        value = EXPANSION_BOUNDARY if w.get_selected() == 0 else EXPANSION_IMMEDIATE
        self._set(lambda s, l: setattr(s, "expansion_key", value))

    def _set_layout(self) -> None:
        value = self.layout_entry.get_text().strip()
        self._set(lambda s, l: (setattr(l, "xkb_layout", value), setattr(l, "xkb_variant", "")))
        self.io_status.set_text("Layout saved. Restart the service for it to take effect: "
                                "systemctl --user restart keyflow" if value else
                                "Layout override cleared; it will be detected automatically.")

    # -- sync -----------------------------------------------------------------------------------

    def _on_sync_toggle(self, w: Gtk.Switch, _p) -> None:
        if self._loading:
            return
        if w.get_active():
            if not util.get_gui_flag("syncExplainerShown"):
                show_sync_explainer(self.win, lambda: util.set_gui_flag("syncExplainerShown"))
            if not self.win.linux.sync_folder:
                self.pick_sync_folder(turning_on=True)
                return
        self._set(lambda s, l: setattr(l, "sync_enabled", w.get_active()))
        if w.get_active():
            self.run_sync()

    def pick_sync_folder(self, turning_on: bool = False) -> None:
        def chosen(path: str) -> None:
            self.set_sync_folder(path)

        def cancelled_revert() -> None:
            if turning_on and not self.win.linux.sync_folder:
                self._loading = True
                self.sync_switch.set_active(False)
                self._loading = False

        util.FilePicker.folder(self.win, "Choose the Syncthing folder shared with your other devices", chosen)
        if turning_on:
            GLib.timeout_add(300, lambda: (cancelled_revert(), False)[1])

    def set_sync_folder(self, path: str) -> None:
        def apply(s, l) -> None:
            l.sync_folder, l.sync_enabled = path, True

        self._set(apply)
        self.run_sync()

    def run_sync(self) -> None:
        folder = self.win.linux.sync_folder
        if not folder:
            return
        self.sync_now.set_sensitive(False)
        self.last_synced.set_text("Syncing…")

        def work() -> None:
            result = SyncEngine(self.win.store, folder).sync_once()
            GLib.idle_add(lambda: (self._sync_done(result), False)[1])

        threading.Thread(target=work, daemon=True).start()

    def _sync_done(self, result) -> None:
        self.sync_now.set_sensitive(True)
        self.win.reload()
        if not result.ok:
            self.sync_error.set_text(result.message)

    # -- import / export ------------------------------------------------------------------------

    def export(self, fmt: str) -> None:
        text = self.win.service.export_text(fmt)

        def write(path: str) -> None:
            try:
                Path(path).write_text(text, encoding="utf-8")
                self.io_status.set_text(f"Exported to {Path(path).name}.")
            except OSError as e:
                self.io_status.set_text(f"Export failed: {e}")

        label_text, pattern = ("JSON", "*.json") if fmt == "json" else ("CSV", "*.csv")
        util.FilePicker.save(self.win, f"Export {label_text}", f"keyflow_export.{fmt}", [(label_text, [pattern])], write)

    def choose_import(self) -> None:
        util.FilePicker.open(self.win, "Import shortcuts", [("JSON or CSV", ["*.json", "*.csv", "*.txt"])],
                             self.import_path)

    def import_path(self, path: str) -> None:
        try:
            text = Path(path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            self.io_status.set_text(f"Import failed: {e}")
            return
        outcome = self.win.service.import_text(text, self.overwrite.get_active())
        self.io_status.set_text("")
        self.win.reload()
        show_import_summary(self.win, outcome)

    # -- refresh --------------------------------------------------------------------------------------

    def refresh(self) -> None:
        win = self.win
        s, l = win.settings, win.linux
        self._loading = True
        self.active.set_active(s.service_enabled)
        self.case_sensitive.set_active(s.case_sensitive)
        self.preserve_case.set_active(s.preserve_case)
        self.preserve_case.set_sensitive(not s.case_sensitive)
        self.expand_on.set_selected(0 if s.expansion_key == EXPANSION_BOUNDARY else 1)
        if self.prefix.get_text() != s.trigger_prefix:
            self.prefix.set_text(s.trigger_prefix)
        if not self.layout_entry.has_focus() and self.layout_entry.get_text() != l.xkb_layout:
            self.layout_entry.set_text(l.xkb_layout)
        names, source = detect_layout(l)
        self.layout_detected.set_text(f"{names.describe()}  ({source})")

        self.sync_switch.set_active(l.sync_enabled and bool(l.sync_folder))
        self.sync_folder.set_text(l.sync_folder or "No folder selected")
        self.choose_folder.set_label("Change…" if l.sync_folder else "Choose Folder…")
        self.sync_now.set_sensitive(l.sync_enabled and bool(l.sync_folder))
        state = SyncState.load(win.store.sync_state_path)
        self.last_synced.set_text(util.relative_time(state.last_synced_ms))
        self.sync_error.set_text(state.last_error or "")
        self._loading = False

        d = win.diagnosis
        if d is None or d.service_active is None:
            self.service_status.set_text("unknown")
        else:
            self.service_status.set_text("running" if d.service_active else "not running")
        self.banner.update(d)
