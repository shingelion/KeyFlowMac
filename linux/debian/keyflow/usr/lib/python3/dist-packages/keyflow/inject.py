"""Types an expansion back through a virtual keyboard (/dev/uinput).

This works identically on X11, Wayland and the console because the kernel input layer sits below
all of them. The text is typed with the same keyboard layout the user types with (see ``xkb``);
characters that layout cannot produce (emoji, other scripts, dead-key-only symbols) fall back to
a clipboard paste, which is the one case where the clipboard is touched.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
import unicodedata
from typing import Callable, Optional, Protocol

from . import keys
from .processor import Expansion
from .xkb import KeyStroke

log = logging.getLogger("keyflow")

VIRTUAL_DEVICE_NAME = "KeyFlow virtual keyboard"


class UInputUnavailable(Exception):
    """/dev/uinput is missing or not writable by this user."""


class Backend(Protocol):
    def emit(self, code: int, value: int) -> None: ...
    def sync(self) -> None: ...


class UInputBackend:
    def __init__(self, key_codes: set[int]) -> None:
        from evdev import UInput, ecodes  # imported lazily: only the daemon needs python-evdev
        from evdev.uinput import UInputError

        try:
            self._ui = UInput({ecodes.EV_KEY: sorted(key_codes)}, name=VIRTUAL_DEVICE_NAME)
        except (OSError, UInputError) as e:
            raise UInputUnavailable(str(e)) from e
        self._ecodes = ecodes

    def emit(self, code: int, value: int) -> None:
        self._ui.write(self._ecodes.EV_KEY, code, value)

    def sync(self) -> None:
        self._ui.syn()

    def close(self) -> None:
        self._ui.close()


class Clipboard:
    """Best-effort clipboard paste via wl-clipboard (Wayland) or xclip (X11)."""

    def __init__(self) -> None:
        self.copy_cmd: Optional[list[str]] = None
        self.paste_cmd: Optional[list[str]] = None
        if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
            self.copy_cmd = ["wl-copy"]
            self.paste_cmd = ["wl-paste", "--no-newline"] if shutil.which("wl-paste") else None
        elif os.environ.get("DISPLAY") and shutil.which("xclip"):
            self.copy_cmd = ["xclip", "-selection", "clipboard", "-in"]
            self.paste_cmd = ["xclip", "-selection", "clipboard", "-out"]

    @property
    def available(self) -> bool:
        return self.copy_cmd is not None

    def get(self) -> Optional[bytes]:
        if not self.paste_cmd:
            return None
        try:
            r = subprocess.run(self.paste_cmd, capture_output=True, timeout=1)
            return r.stdout if r.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            return None

    def set(self, data: bytes) -> bool:
        if not self.copy_cmd:
            return False
        try:
            # wl-copy / xclip fork into the background to keep serving the selection.
            subprocess.run(self.copy_cmd, input=data, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=2)
            return True
        except (OSError, subprocess.SubprocessError):
            return False


class Injector:
    def __init__(
        self,
        backend: Backend,
        reverse_map: dict[str, KeyStroke],
        dead_keys: Optional[dict[str, KeyStroke]] = None,
        delay_seconds: float = 0.002,
        clipboard: Optional[Clipboard] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.backend = backend
        self.reverse_map = reverse_map
        self.dead_keys = dead_keys or {}
        self.delay = delay_seconds
        self.clipboard = clipboard
        self._sleep = sleep

    @staticmethod
    def required_key_codes(*maps: dict[str, KeyStroke]) -> set[int]:
        codes = {s.code for m in maps for s in m.values()}
        codes.update({keys.KEY_LEFTSHIFT, keys.KEY_RIGHTALT, keys.KEY_BACKSPACE, keys.KEY_ENTER,
                      keys.KEY_TAB, keys.KEY_LEFT, keys.KEY_INSERT})
        return codes

    # -- primitives -------------------------------------------------------------------------

    def _press(self, code: int, down: bool) -> None:
        self.backend.emit(code, 1 if down else 0)
        self.backend.sync()
        if self.delay:
            self._sleep(self.delay)

    def tap(self, code: int, shift: bool = False, altgr: bool = False) -> None:
        if shift:
            self._press(keys.KEY_LEFTSHIFT, True)
        if altgr:
            self._press(keys.KEY_RIGHTALT, True)
        self._press(code, True)
        self._press(code, False)
        if altgr:
            self._press(keys.KEY_RIGHTALT, False)
        if shift:
            self._press(keys.KEY_LEFTSHIFT, False)

    def _strokes_for(self, ch: str) -> Optional[list[KeyStroke]]:
        """Key strokes that produce ``ch``, or None if this layout cannot type it.

        Accented letters missing from the layout are composed the way the user would: dead key,
        then base letter (e.g. circumflex + o -> ô on a French keyboard).
        """
        if ch == "\n":
            return [KeyStroke(keys.KEY_ENTER, False, False)]
        if ch == "\t":
            return [KeyStroke(keys.KEY_TAB, False, False)]
        direct = self.reverse_map.get(ch)
        if direct is not None:
            return [direct]
        decomposed = unicodedata.normalize("NFD", ch)
        if len(decomposed) == 2:
            base, accent = self.reverse_map.get(decomposed[0]), self.dead_keys.get(decomposed[1])
            if base is not None and accent is not None:
                return [accent, base]
        return None

    def can_type(self, text: str) -> bool:
        return all(self._strokes_for(c) is not None for c in text)

    def type_text(self, text: str) -> str:
        """Type ``text``; returns the characters this layout cannot produce (and were skipped)."""
        skipped = []
        for ch in text:
            strokes = self._strokes_for(ch)
            if strokes is None:
                skipped.append(ch)
                continue
            for stroke in strokes:
                self.tap(*stroke)
        return "".join(skipped)

    def _paste(self, text: str) -> bool:
        cb = self.clipboard
        if cb is None or not cb.available:
            return False
        previous = cb.get()
        if not cb.set(text.encode("utf-8")):
            return False
        self._sleep(0.05)
        self.tap(keys.KEY_INSERT, shift=True)  # Shift+Insert pastes in terminals and GUI apps alike
        self._sleep(0.25)  # let the target read the selection before we put the old one back
        if previous is not None:
            cb.set(previous)
        return True

    # -- the whole expansion ----------------------------------------------------------------

    def perform(self, exp: Expansion) -> None:
        for _ in range(exp.delete_count):
            self.tap(keys.KEY_BACKSPACE)

        if self.can_type(exp.text):
            self.type_text(exp.text)
        elif not self._paste(exp.text):
            lost = self.type_text(exp.text)
            log.warning(
                "%d character(s) are not on the current keyboard layout and were skipped; "
                "install wl-clipboard (Wayland) or xclip (X11) to paste them instead",
                len(lost),
            )
        self.type_text(exp.trailing)

        if exp.cursor_offset is not None:
            for _ in range(len(exp.text) - exp.cursor_offset + len(exp.trailing)):
                self.tap(keys.KEY_LEFT)
