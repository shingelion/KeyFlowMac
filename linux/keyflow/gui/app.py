"""Gtk.Application: one window, single instance, plus the quick-add entry point."""

from __future__ import annotations

from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from ..store import Store  # noqa: E402
from . import util  # noqa: E402
from .window import MainWindow  # noqa: E402

APP_ID = "org.keyflow.KeyFlow"


class KeyFlowApp(Gtk.Application):
    def __init__(self, store: Optional[Store] = None) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.store = store or Store()
        self.window: Optional[MainWindow] = None
        GLib.set_prgname(APP_ID)

    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        util.install_css()
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Primary>q"])

    def show_main(self) -> MainWindow:
        if self.window is None:
            self.window = MainWindow(self, self.store)
            self.window.connect("close-request", self._on_close)
        self.window.present()
        return self.window

    def _on_close(self, _w) -> bool:
        self.window = None
        return False

    def do_command_line(self, cmdline: Gio.ApplicationCommandLine) -> int:
        args = cmdline.get_arguments()[1:]
        win = self.show_main()
        if "--quick-add" in args:
            if "--from-selection" in args:
                self._quick_add_from_selection(win)
            else:
                win.quick_add()
        return 0

    @staticmethod
    def _quick_add_from_selection(win: MainWindow) -> None:
        """Pre-fill the expansion with the text selected in another app (the PRIMARY selection),
        the counterpart of the macOS "Add as KeyFlow Shortcut" Services entry."""
        display = Gdk.Display.get_default()
        clipboard = display.get_primary_clipboard()

        def got(source, result) -> None:
            try:
                text = source.read_text_finish(result) or ""
            except GLib.Error:
                text = ""
            win.quick_add(text)

        clipboard.read_text_async(None, got)
