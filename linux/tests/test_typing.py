"""End-to-end: synthetic key events -> processor -> injector -> replayed on a virtual text field.

The replay uses libxkbcommon's forward translation, i.e. the opposite direction from the one the
injector's reverse map was built with, so a passing test means the typed text really comes out
as intended on that layout.
"""

import unicodedata
import unittest

from keyflow import keys
from keyflow.core import AppSettings, ExpansionEngine, Shortcut
from keyflow.inject import Injector
from keyflow.processor import KeystrokeProcessor
from keyflow.xkb import KeymapNames, XkbKeymap


class FakeField:
    """Receives emitted key events like an app would and keeps the resulting text + caret."""

    def __init__(self, keymap: XkbKeymap) -> None:
        self.state = keymap.new_state()
        self.text = ""
        self.caret = 0
        self.pending_accent = ""
        self._dead = {(k.code, k.shift, k.altgr): accent for accent, k in keymap.dead_keys().items()}

    def emit(self, code: int, value: int) -> None:
        self.state.update(code, bool(value))
        if value != 1:
            return
        if code == keys.KEY_BACKSPACE:
            if self.caret:
                self.text = self.text[: self.caret - 1] + self.text[self.caret:]
                self.caret -= 1
        elif code == keys.KEY_LEFT:
            self.caret = max(0, self.caret - 1)
        elif code == keys.KEY_ENTER:
            self.insert("\n")
        elif code == keys.KEY_TAB:
            self.insert("\t")
        else:
            accent = self._dead.get(
                (code, self.state.mod_active("Shift"), self.state.mod_active("Mod5")))
            if accent and not self.state.utf8(code):
                self.pending_accent = accent
                return
            typed = self.state.utf8(code)
            if not typed:
                return  # a modifier: must not cancel a pending dead key
            if self.pending_accent:
                typed = unicodedata.normalize("NFC", typed + self.pending_accent)
            self.pending_accent = ""
            self.insert(typed)

    def insert(self, s: str) -> None:
        if any(ord(c) < 0x20 and c not in "\n\t" for c in s):
            return  # e.g. Escape: not text
        self.text = self.text[: self.caret] + s + self.text[self.caret:]
        self.caret += len(s)

    def sync(self) -> None:
        pass


class Rig:
    def __init__(self, layout="us", variant="", settings=None, shortcuts=None):
        self.keymap = XkbKeymap(KeymapNames(layout=layout, variant=variant))
        self.field = FakeField(self.keymap)
        engine = ExpansionEngine()
        engine.update_shortcuts(shortcuts or [])
        self.settings = settings or AppSettings()
        self.proc = KeystrokeProcessor(engine, self.keymap.new_state(), self.settings)
        self.injector = Injector(self.field, self.keymap.reverse_map(), self.keymap.dead_keys(),
                                 delay_seconds=0, sleep=lambda _: None)
        self.reverse = self.keymap.reverse_map()

    def physical(self, code: int, down: bool):
        """A physical key event: the app sees it and so does the processor."""
        self.field.emit(code, 1 if down else 0)
        return self.proc.on_key(code, 1 if down else 0)

    def type_and_expand(self, text: str):
        for ch in text:
            stroke = self.reverse[ch]
            if stroke.shift:
                self.physical(keys.KEY_LEFTSHIFT, True)
            if stroke.altgr:
                self.physical(keys.KEY_RIGHTALT, True)
            expansion = self.physical(stroke.code, True)
            self.physical(stroke.code, False)
            if stroke.altgr:
                self.physical(keys.KEY_RIGHTALT, False)
            if stroke.shift:
                self.physical(keys.KEY_LEFTSHIFT, False)
            if expansion:
                self.injector.perform(expansion)
                self.proc.clear()


class TypingTests(unittest.TestCase):
    def rig(self, **kw):
        r = Rig(**kw)
        self.addCleanup(r.keymap.close)
        return r

    def test_boundary_expansion_keeps_the_boundary_character(self):
        r = self.rig(shortcuts=[Shortcut(";addr", "123 Main St")])
        r.type_and_expand("Hi ;addr ")
        self.assertEqual(r.field.text, "Hi 123 Main St ")

    def test_immediate_mode(self):
        r = self.rig(settings=AppSettings(expansion_key="immediate"), shortcuts=[Shortcut(";addr", "123 Main St")])
        r.type_and_expand("Hi ;addr")
        self.assertEqual(r.field.text, "Hi 123 Main St")

    def test_punctuation_boundary_is_retyped(self):
        r = self.rig(shortcuts=[Shortcut("sig", "Best, Erwan")])
        r.type_and_expand("Thanks sig!")
        self.assertEqual(r.field.text, "Thanks Best, Erwan!")

    def test_case_adaptation(self):
        r = self.rig(shortcuts=[Shortcut(";addr", "hello there")])
        r.type_and_expand(";Addr ")
        self.assertEqual(r.field.text, "Hello there ")
        r2 = self.rig(shortcuts=[Shortcut(";addr", "hello there")])
        r2.type_and_expand(";ADDR ")
        self.assertEqual(r2.field.text, "HELLO THERE ")

    def test_cursor_placeholder_and_multiline(self):
        r = self.rig(shortcuts=[Shortcut(";d", "Dear {cursor},\nBest")])
        r.type_and_expand(";d ")
        self.assertEqual(r.field.text, "Dear ,\nBest ")
        self.assertEqual(r.field.caret, len("Dear "))

    def test_azerty_layout_with_accents_and_altgr(self):
        r = self.rig(layout="fr", shortcuts=[Shortcut(";mail", "érwann@exemple.fr à bientôt € [ok] Ôte")])
        r.type_and_expand(";mail ")
        self.assertEqual(r.field.text, "érwann@exemple.fr à bientôt € [ok] Ôte ")

    def test_qwertz_layout(self):
        r = self.rig(layout="de", shortcuts=[Shortcut(";z", "Zürich: y/z {x} ß")])
        r.type_and_expand(";z ")
        self.assertEqual(r.field.text, "Zürich: y/z {x} ß ")

    def test_backspace_corrects_the_buffer(self):
        r = self.rig(shortcuts=[Shortcut(";addr", "X")])
        r.type_and_expand(";adx")
        r.physical(keys.KEY_BACKSPACE, True); r.physical(keys.KEY_BACKSPACE, False)
        r.type_and_expand("dr ")
        self.assertEqual(r.field.text, "X ")

    def test_navigation_key_clears_buffer(self):
        r = self.rig(shortcuts=[Shortcut(";addr", "X")])
        r.type_and_expand(";ad")
        r.physical(keys.KEY_ESC, True); r.physical(keys.KEY_ESC, False)
        r.type_and_expand("dr ")
        self.assertEqual(r.field.text, ";addr ")

    def test_ctrl_shortcut_and_click_clear_buffer(self):
        r = self.rig(shortcuts=[Shortcut(";addr", "X")])
        r.type_and_expand(";ad")
        r.physical(keys.KEY_LEFTCTRL, True); r.physical(keys.KEY_LEFTCTRL, False)
        r.type_and_expand("dr ")
        self.assertEqual(r.field.text, ";addr ")
        r2 = self.rig(shortcuts=[Shortcut(";addr", "X")])
        r2.type_and_expand(";ad")
        r2.proc.on_pointer()
        r2.type_and_expand("dr ")
        self.assertEqual(r2.field.text, ";addr ")

    def test_paused_service_does_nothing(self):
        r = self.rig(settings=AppSettings(service_enabled=False), shortcuts=[Shortcut(";addr", "X")])
        r.type_and_expand(";addr ")
        self.assertEqual(r.field.text, ";addr ")

    def test_idle_timeout_clears_buffer(self):
        now = [0.0]
        r = self.rig(shortcuts=[Shortcut(";addr", "X")])
        r.proc._clock = lambda: now[0]
        r.type_and_expand(";ad")
        now[0] = 100.0
        r.type_and_expand("dr ")
        self.assertEqual(r.field.text, ";addr ")

    def test_held_keys_are_tracked(self):
        r = self.rig(shortcuts=[])
        r.physical(keys.KEY_LEFTSHIFT, True)
        self.assertIn(keys.KEY_LEFTSHIFT, r.proc.held)
        r.physical(keys.KEY_LEFTSHIFT, False)
        self.assertFalse(r.proc.held)

    def test_unmappable_characters_without_clipboard_are_skipped(self):
        r = self.rig(shortcuts=[Shortcut(";e", "a😀b")])
        r.type_and_expand(";e ")
        self.assertEqual(r.field.text, "ab ")

    def test_clipboard_fallback_used_for_unmappable_characters(self):
        r = self.rig(shortcuts=[Shortcut(";e", "a😀b")])
        pasted = []

        class FakeClipboard:
            available = True
            def get(self): return b"old"
            def set(self, data): pasted.append(data); return True

        r.injector.clipboard = FakeClipboard()
        r.type_and_expand(";e ")
        self.assertEqual(pasted, ["a😀b".encode(), b"old"])  # pasted, then clipboard restored
        self.assertEqual(r.field.text, " ")  # the fake field has no clipboard; the paste key was sent


if __name__ == "__main__":
    unittest.main()
