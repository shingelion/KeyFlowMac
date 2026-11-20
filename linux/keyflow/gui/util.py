"""Small helpers shared by the GTK widgets (no state, no I/O except file dialogs)."""

from __future__ import annotations

import json
import re
import time
from typing import Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from ..core import Shortcut  # noqa: E402
from ..store import config_dir  # noqa: E402

CSS = """
.kf-chip { border-radius: 999px; padding: 2px 12px; min-height: 0; }
.kf-chip:checked { background: @theme_selected_bg_color; color: @theme_selected_fg_color; }
.kf-banner { background: alpha(#f5c211, 0.22); border-radius: 8px; padding: 10px 12px; }
.kf-banner-ok { background: alpha(#33d17a, 0.18); }
.kf-row-selected { background: alpha(@theme_selected_bg_color, 0.22); }
.kf-card { border: 1px solid alpha(currentColor, 0.15); border-radius: 8px; }
.kf-card row { padding: 8px 12px; }
.kf-editor { border: 1px solid alpha(currentColor, 0.22); border-radius: 6px; }
.kf-swatch { min-width: 14px; min-height: 14px; border-radius: 7px; }
.kf-swatch-none { border: 1px solid alpha(currentColor, 0.4); }
.kf-trigger { font-weight: bold; }
.kf-mono { font-family: monospace; }
.kf-section { font-weight: bold; }
"""

_installed_color_classes: set[str] = set()
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def install_css() -> None:
    provider = Gtk.CssProvider()
    provider.load_from_string(CSS) if hasattr(provider, "load_from_string") else provider.load_from_data(CSS.encode())
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                              Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def color_class(hex_color: Optional[str]) -> Optional[str]:
    """CSS class painting a background in ``hex_color`` (created on first use)."""
    if not hex_color or not _HEX.match(hex_color):
        return None
    name = "kf-c-" + hex_color[1:].lower()
    if name not in _installed_color_classes:
        provider = Gtk.CssProvider()
        css = f".{name} {{ background: {hex_color}; }}"
        provider.load_from_string(css) if hasattr(provider, "load_from_string") else provider.load_from_data(css.encode())
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        _installed_color_classes.add(name)
    return name


def swatch(hex_color: Optional[str]) -> Gtk.Widget:
    w = Gtk.Box(valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
    w.add_css_class("kf-swatch")
    cls = color_class(hex_color)
    w.add_css_class(cls if cls else "kf-swatch-none")
    return w


def shortcut_key(s: Shortcut) -> str:
    """Stable identity of a shortcut across reloads, for selection state."""
    return s.sync_id or "t:" + s.trigger.lower()


def one_line(text: str) -> str:
    return text.replace("\n", " ")


def relative_time(ms: Optional[int], now: Optional[float] = None) -> str:
    if not ms:
        return "Never synced"
    secs = max(0, int((now if now is not None else time.time()) - ms / 1000))
    if secs < 10:
        return "Last synced just now"
    for limit, unit, div in ((60, "second", 1), (3600, "minute", 60), (86400, "hour", 3600), (None, "day", 86400)):
        if limit is None or secs < limit:
            n = secs // div
            return f"Last synced {n} {unit}{'' if n == 1 else 's'} ago"
    return "Last synced a while ago"


# -- tiny persistent GUI state (one-time hints) ---------------------------------------------


def _gui_state_path():
    return config_dir() / "gui-state.json"


def get_gui_flag(name: str) -> bool:
    try:
        return bool(json.loads(_gui_state_path().read_text()).get(name))
    except (OSError, ValueError, AttributeError):
        return False


def set_gui_flag(name: str, value: bool = True) -> None:
    try:
        path = _gui_state_path()
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            data = {}
        data[name] = value
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
    except OSError:
        pass


# -- widgets / dialogs ------------------------------------------------------------------------


def label(text: str = "", *, dim: bool = False, bold: bool = False, wrap: bool = True,
          mono: bool = False, selectable: bool = False) -> Gtk.Label:
    lb = Gtk.Label(label=text, xalign=0, wrap=wrap, selectable=selectable)
    if dim:
        lb.add_css_class("dim-label")
    if bold:
        lb.add_css_class("kf-section")
    if mono:
        lb.add_css_class("kf-mono")
    return lb


def make_window(parent: Optional[Gtk.Window], title: str, width: int = 420, height: int = -1) -> Gtk.Window:
    win = Gtk.Window(title=title, modal=parent is not None, default_width=width, default_height=height)
    if parent is not None:
        win.set_transient_for(parent)
    key = Gtk.EventControllerKey()
    key.connect("key-pressed", lambda _c, keyval, _kc, _st: (win.close(), True)[1] if keyval == Gdk.KEY_Escape else False)
    win.add_controller(key)
    return win


def padded_box(spacing: int = 12, margin: int = 20) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
    for side in ("start", "end", "top", "bottom"):
        getattr(box, f"set_margin_{side}")(margin)
    return box


def button_row(*buttons: Gtk.Widget, start: Optional[Gtk.Widget] = None) -> Gtk.Box:
    row = Gtk.Box(spacing=8)
    if start is not None:
        row.append(start)
    row.append(Gtk.Box(hexpand=True))
    for b in buttons:
        row.append(b)
    return row


def confirm(parent: Gtk.Window, title: str, body: str, choices: list[tuple[str, Optional[str], Callable[[], None]]]) -> Gtk.Window:
    """Modal question. ``choices`` are (label, style, callback); style is "destructive",
    "suggested" or None. A Cancel button is always added."""
    win = make_window(parent, title, 380)
    box = padded_box()
    box.append(label(title, bold=True))
    if body:
        box.append(label(body, dim=True))
    buttons = []
    cancel = Gtk.Button(label="Cancel")
    cancel.connect("clicked", lambda _b: win.close())
    buttons.append(cancel)
    for text, style, cb in choices:
        b = Gtk.Button(label=text)
        if style:
            b.add_css_class({"destructive": "destructive-action", "suggested": "suggested-action"}[style])
        b.connect("clicked", lambda _b, cb=cb: (win.close(), cb()))
        buttons.append(b)
    box.append(button_row(*buttons))
    win.set_child(box)
    win.present()
    return win


def prompt_text(parent: Gtk.Window, title: str, placeholder: str, initial: str,
                ok_label: str, on_ok: Callable[[str], Optional[str]]) -> Gtk.Window:
    """Ask for one line of text. ``on_ok`` returns an error message to keep the dialog open."""
    win = make_window(parent, title, 340)
    box = padded_box()
    box.append(label(title, bold=True))
    entry = Gtk.Entry(placeholder_text=placeholder, text=initial, activates_default=True)
    box.append(entry)
    error = label("", wrap=True)
    error.add_css_class("error")
    box.append(error)
    ok = Gtk.Button(label=ok_label)
    ok.add_css_class("suggested-action")
    cancel = Gtk.Button(label="Cancel")
    cancel.connect("clicked", lambda _b: win.close())

    def submit(_w=None) -> None:
        message = on_ok(entry.get_text())
        if message:
            error.set_text(message)
        else:
            win.close()

    ok.connect("clicked", submit)
    entry.connect("activate", submit)
    box.append(button_row(cancel, ok))
    win.set_child(box)
    win.set_default_widget(ok)
    win.present()
    entry.grab_focus()
    return win


class FilePicker:
    """Thin wrapper around Gtk.FileChooserNative that keeps a reference until it answers."""

    _live: set = set()

    @classmethod
    def _open(cls, parent, title, action, accept, filters, name, cb) -> None:
        dialog = Gtk.FileChooserNative.new(title, parent, action, accept, "Cancel")
        for text, patterns in filters:
            f = Gtk.FileFilter()
            f.set_name(text)
            for p in patterns:
                f.add_pattern(p)
            dialog.add_filter(f)
        if name:
            dialog.set_current_name(name)
        cls._live.add(dialog)

        def done(d, response):
            cls._live.discard(d)
            gfile = d.get_file() if response == Gtk.ResponseType.ACCEPT else None
            path = gfile.get_path() if gfile else None
            if path:
                cb(path)

        dialog.connect("response", done)
        dialog.show()

    @classmethod
    def save(cls, parent, title, suggested_name, filters, cb) -> None:
        cls._open(parent, title, Gtk.FileChooserAction.SAVE, "Save", filters, suggested_name, cb)

    @classmethod
    def open(cls, parent, title, filters, cb) -> None:
        cls._open(parent, title, Gtk.FileChooserAction.OPEN, "Open", filters, None, cb)

    @classmethod
    def folder(cls, parent, title, cb) -> None:
        cls._open(parent, title, Gtk.FileChooserAction.SELECT_FOLDER, "Choose", [], None, cb)


def idle(fn: Callable[[], None]) -> None:
    GLib.idle_add(lambda: (fn(), False)[1])
