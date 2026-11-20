"""GTK 4 graphical interface (``keyflow gui``)."""

from __future__ import annotations

import sys
from typing import Optional


def _missing_gtk_help() -> str:
    from ..cli import distro_ids

    ids = distro_ids()
    if "arch" in ids:
        cmd = "sudo pacman -S python-gobject gtk4"
    elif "fedora" in ids or "rhel" in ids:
        cmd = "sudo dnf install python3-gobject gtk4"
    elif ids & {"debian", "ubuntu"}:
        cmd = "sudo apt install python3-gi gir1.2-gtk-4.0"
    else:
        cmd = "install PyGObject and GTK 4 with your package manager"
    return f"The KeyFlow window needs GTK 4 for Python.\n  {cmd}"


def run(argv: Optional[list[str]] = None) -> int:
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Gdk", "4.0")
        from gi.repository import Gtk  # noqa: F401
    except (ImportError, ValueError):
        print(_missing_gtk_help(), file=sys.stderr)
        return 1
    from .app import KeyFlowApp

    return KeyFlowApp().run(["keyflow"] + list(argv or []))
