"""Turns raw key events into expansions.

This is the Linux counterpart of ``KeystrokeMonitor.handleKeyDown``. It is deliberately free of
any device I/O so it can be driven by synthetic events in tests.

Unlike macOS there is no "read the focused field" API on Linux (and, on Wayland, no way to ask
which window has focus), so a private buffer of recently typed characters is kept and cleared
whenever it can no longer be trusted: navigation keys, shortcut modifiers, mouse clicks, or a
long pause.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional, Protocol

from . import keys
from .core import (
    AppSettings,
    ExpansionEngine,
    adapt_case,
    process_placeholders,
)

MAX_BUFFER_LENGTH = 256
#: With no focus signal, a long pause is the best hint that the user went elsewhere.
IDLE_TIMEOUT_SECONDS = 30.0


class KeyState(Protocol):
    def update(self, code: int, down: bool) -> None: ...
    def utf8(self, code: int) -> str: ...
    def mod_active(self, name: str) -> bool: ...


@dataclass(frozen=True)
class Expansion:
    #: Characters to erase: the trigger plus whatever (boundary character) followed it.
    delete_count: int
    #: Replacement text, placeholders resolved and case adapted.
    text: str
    #: Boundary character the user typed after the trigger; re-typed after the text so
    #: "addr<space>" becomes "123 Main St<space>", as in aText.
    trailing: str
    #: Where the caret should land inside ``text`` (``{cursor}``), or None for the end.
    cursor_offset: Optional[int]


class KeystrokeProcessor:
    def __init__(
        self,
        engine: ExpansionEngine,
        state: KeyState,
        settings: Optional[AppSettings] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.engine = engine
        self.state = state
        self.settings = settings or AppSettings()
        self.buffer = ""
        #: Key codes currently held down on any keyboard. The injector waits for this to empty
        #: so held modifiers do not leak into the synthetic keystrokes.
        self.held: set[int] = set()
        self._clock = clock
        self._last_key_at = clock()

    def clear(self) -> None:
        self.buffer = ""

    def on_pointer(self) -> None:
        self.clear()

    def on_key(self, code: int, value: int) -> Optional[Expansion]:
        """Handle one EV_KEY event (value: 0 release, 1 press, 2 autorepeat)."""
        if value == 0:
            self.held.discard(code)
            self.state.update(code, False)
            return None
        if value == 1:
            self.held.add(code)
            self.state.update(code, True)
        if value not in (1, 2):
            return None

        now = self._clock()
        if now - self._last_key_at > IDLE_TIMEOUT_SECONDS:
            self.buffer = ""
        self._last_key_at = now

        if not self.settings.service_enabled:
            self.buffer = ""
            return None

        if code in keys.CLEARING_MODIFIERS:
            self.buffer = ""
            return None
        if code in keys.NEUTRAL_MODIFIERS:
            return None
        if code == keys.KEY_BACKSPACE:
            self.buffer = self.buffer[:-1]
            return None

        if self.state.mod_active("Control") or self.state.mod_active("Mod1") or self.state.mod_active("Mod4"):
            self.buffer = ""
            return None

        typed = self.state.utf8(code)
        # Enter, Tab, Escape, arrows, F-keys, dead keys... anything that is not plain text can
        # move the caret or submit the field, so the buffer is no longer reliable.
        if not typed or any(ord(c) < 0x20 or ord(c) == 0x7F for c in typed):
            self.buffer = ""
            return None

        self.buffer = (self.buffer + typed)[-MAX_BUFFER_LENGTH:]

        match = self.engine.find_match(self.buffer, self.settings)
        if match is None:
            return None

        processed = process_placeholders(match.shortcut.expansion)
        text = processed.text
        if not self.settings.case_sensitive and self.settings.preserve_case:
            text = adapt_case(match.typed_trigger, text)

        result = Expansion(
            delete_count=len(self.buffer) - match.match_start,
            text=text,
            trailing=self.buffer[match.match_end:],
            cursor_offset=processed.cursor_offset,
        )
        self.buffer = ""
        return result
