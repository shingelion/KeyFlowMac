"""The "KeyFlow can't work yet" banner (counterpart of AccessibilityStatusView)."""

from __future__ import annotations

from typing import Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from ..status import Diagnosis  # noqa: E402
from .util import label  # noqa: E402


class Banner(Gtk.Revealer):
    def __init__(self, on_action: Callable[[], None]) -> None:
        super().__init__(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        self._on_action = on_action
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.add_css_class("kf-banner")
        self.message = label("")
        box.append(self.message)
        self.command_row = Gtk.Box(spacing=8)
        self.command = label("", mono=True, selectable=True)
        self.command.set_hexpand(True)
        copy = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text="Copy command")
        copy.add_css_class("flat")
        copy.connect("clicked", lambda _b: self._copy())
        self.command_row.append(self.command)
        self.command_row.append(copy)
        box.append(self.command_row)
        self.action = Gtk.Button(label="", halign=Gtk.Align.START)
        self.action.add_css_class("suggested-action")
        self.action.connect("clicked", lambda _b: self._on_action())
        box.append(self.action)
        self.set_child(box)
        self.set_margin_start(16)
        self.set_margin_end(16)
        self.set_margin_top(8)

    def _copy(self) -> None:
        text = self.command.get_text().split("   #")[0]
        self.get_clipboard().set(text)

    def update(self, diagnosis: Optional[Diagnosis]) -> None:
        problem = diagnosis.problems[0] if diagnosis and diagnosis.problems else None
        self.set_reveal_child(problem is not None)
        if problem is None:
            return
        self.message.set_text(problem.message)
        self.command_row.set_visible(bool(problem.command))
        self.command.set_text(problem.command or "")
        self.action.set_visible(bool(problem.action))
        self.action.set_label(problem.action or "")
