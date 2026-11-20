"""Minimal ctypes binding to libxkbcommon.

The kernel only reports *key codes*; what character a key produces depends on the user's keyboard
layout. libxkbcommon (the same library compositors use) answers both directions:

* key code (+ modifier state) -> character, to know what the user typed;
* character -> key code + modifiers, to type an expansion back on the same layout.

Only the first layout group is used; switching groups at runtime is not tracked.
"""

from __future__ import annotations

import ctypes
import ctypes.util
from dataclasses import dataclass
from typing import NamedTuple, Optional

from . import keys

_EVDEV_OFFSET = 8  # xkb keycodes are evdev codes + 8
_XKB_KEY_UP, _XKB_KEY_DOWN = 0, 1
_XKB_STATE_MODS_EFFECTIVE = 8
_XKB_MOD_INVALID = 0xFFFFFFFF


class XkbError(RuntimeError):
    pass


class _RuleNames(ctypes.Structure):
    _fields_ = [
        ("rules", ctypes.c_char_p),
        ("model", ctypes.c_char_p),
        ("layout", ctypes.c_char_p),
        ("variant", ctypes.c_char_p),
        ("options", ctypes.c_char_p),
    ]


_lib: Optional[ctypes.CDLL] = None


def _xkb() -> ctypes.CDLL:
    global _lib
    if _lib is not None:
        return _lib
    path = ctypes.util.find_library("xkbcommon") or "libxkbcommon.so.0"
    try:
        lib = ctypes.CDLL(path)
    except OSError as e:
        raise XkbError(f"cannot load libxkbcommon ({e}); install the 'libxkbcommon' package") from e

    vp, u32, c_int, c_sz = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_size_t
    sig = {
        "xkb_context_new": (vp, [c_int]),
        "xkb_context_unref": (None, [vp]),
        "xkb_keymap_new_from_names": (vp, [vp, ctypes.POINTER(_RuleNames), c_int]),
        "xkb_keymap_unref": (None, [vp]),
        "xkb_keymap_min_keycode": (u32, [vp]),
        "xkb_keymap_max_keycode": (u32, [vp]),
        "xkb_keymap_num_layouts_for_key": (u32, [vp, u32]),
        "xkb_keymap_num_levels_for_key": (u32, [vp, u32, u32]),
        "xkb_keymap_key_get_syms_by_level": (
            c_int, [vp, u32, u32, u32, ctypes.POINTER(ctypes.POINTER(u32))]),
        "xkb_keymap_key_get_mods_for_level": (
            c_sz, [vp, u32, u32, u32, ctypes.POINTER(u32), c_sz]),
        "xkb_keysym_to_utf32": (u32, [u32]),
        "xkb_state_new": (vp, [vp]),
        "xkb_state_unref": (None, [vp]),
        "xkb_state_update_key": (c_int, [vp, u32, c_int]),
        "xkb_state_key_get_utf8": (c_int, [vp, u32, ctypes.c_char_p, c_sz]),
        "xkb_state_serialize_mods": (u32, [vp, c_int]),
        "xkb_state_mod_name_is_active": (c_int, [vp, ctypes.c_char_p, c_int]),
    }
    for name, (res, args) in sig.items():
        fn = getattr(lib, name)
        fn.restype, fn.argtypes = res, args
    _lib = lib
    return lib


@dataclass(frozen=True)
class KeymapNames:
    """XKB rule names, i.e. what ``setxkbmap -query`` / ``localectl`` report."""

    layout: str = "us"
    variant: str = ""
    model: str = ""
    options: str = ""
    rules: str = ""

    def describe(self) -> str:
        parts = [self.layout]
        if self.variant:
            parts.append(f"variant={self.variant}")
        if self.options:
            parts.append(f"options={self.options}")
        return " ".join(parts)


#: Dead-key keysyms -> the combining character they add to the next letter.
_DEAD_KEYSYMS = {
    0xFE50: "\u0300",  # dead_grave
    0xFE51: "\u0301",  # dead_acute
    0xFE52: "\u0302",  # dead_circumflex
    0xFE53: "\u0303",  # dead_tilde
    0xFE57: "\u0308",  # dead_diaeresis
    0xFE5A: "\u030C",  # dead_caron
    0xFE5B: "\u0327",  # dead_cedilla
}


class KeyStroke(NamedTuple):
    code: int  # evdev key code
    shift: bool
    altgr: bool


class XkbKeymap:
    def __init__(self, names: KeymapNames) -> None:
        lib = _xkb()
        self._ctx = lib.xkb_context_new(0)
        if not self._ctx:
            raise XkbError("xkb_context_new failed")

        def enc(s: str) -> Optional[bytes]:
            return s.encode() if s else None

        rn = _RuleNames(enc(names.rules), enc(names.model), enc(names.layout), enc(names.variant), enc(names.options))
        self._keymap = lib.xkb_keymap_new_from_names(self._ctx, ctypes.byref(rn), 0)
        if not self._keymap:
            lib.xkb_context_unref(self._ctx)
            raise XkbError(f"no such keyboard layout: {names.describe()}")
        self.names = names
        self._built: Optional[tuple[dict[str, KeyStroke], dict[str, KeyStroke]]] = None

    def close(self) -> None:
        lib = _xkb()
        if self._keymap:
            lib.xkb_keymap_unref(self._keymap)
            self._keymap = None
        if self._ctx:
            lib.xkb_context_unref(self._ctx)
            self._ctx = None

    def new_state(self) -> "XkbState":
        return XkbState(self)

    def _modifier_masks(self) -> tuple[int, int]:
        """Modifier masks produced by the Shift and AltGr (right Alt) keys on this layout."""
        masks = []
        for code in (keys.KEY_LEFTSHIFT, keys.KEY_RIGHTALT):
            st = self.new_state()
            st.update(code, True)
            masks.append(st.mods())
            st.close()
        return masks[0], masks[1]

    def reverse_map(self) -> dict[str, KeyStroke]:
        """Map each directly typeable character to the cheapest key stroke that produces it."""
        return self._build()[0]

    def dead_keys(self) -> dict[str, KeyStroke]:
        """Map combining accents (U+0300 grave, U+0301 acute, ...) to the dead key that adds them."""
        return self._build()[1]

    def _build(self) -> tuple[dict[str, KeyStroke], dict[str, KeyStroke]]:
        if self._built is not None:
            return self._built
        lib = _xkb()
        shift_mask, altgr_mask = self._modifier_masks()
        allowed: dict[int, tuple[bool, bool]] = {0: (False, False)}
        if shift_mask:
            allowed[shift_mask] = (True, False)
        if altgr_mask:
            allowed[altgr_mask] = (False, True)
        if shift_mask and altgr_mask:
            allowed[shift_mask | altgr_mask] = (True, True)

        best: dict[str, tuple[tuple[int, int], KeyStroke]] = {}
        best_dead: dict[str, tuple[tuple[int, int], KeyStroke]] = {}
        lo, hi = lib.xkb_keymap_min_keycode(self._keymap), lib.xkb_keymap_max_keycode(self._keymap)
        masks_buf = (ctypes.c_uint32 * 8)()
        for kc in range(lo, hi + 1):
            if lib.xkb_keymap_num_layouts_for_key(self._keymap, kc) == 0:
                continue
            for level in range(lib.xkb_keymap_num_levels_for_key(self._keymap, kc, 0)):
                syms = ctypes.POINTER(ctypes.c_uint32)()
                if lib.xkb_keymap_key_get_syms_by_level(self._keymap, kc, 0, level, ctypes.byref(syms)) != 1:
                    continue
                cp = lib.xkb_keysym_to_utf32(syms[0])
                if cp == 0 and syms[0] not in _DEAD_KEYSYMS:
                    continue
                if 0 < cp < 0x20 or cp == 0x7F:
                    continue  # control characters
                n = lib.xkb_keymap_key_get_mods_for_level(self._keymap, kc, 0, level, masks_buf, 8)
                options = [masks_buf[i] for i in range(n) if masks_buf[i] in allowed]
                if not options:
                    continue
                mask = min(options, key=lambda m: bin(m).count("1"))
                shift, altgr = allowed[mask]
                stroke = KeyStroke(kc - _EVDEV_OFFSET, shift, altgr)
                cost = (int(shift) + int(altgr), kc)
                if cp == 0:
                    table, ch = best_dead, _DEAD_KEYSYMS[syms[0]]
                else:
                    table, ch = best, chr(cp)
                if ch not in table or cost < table[ch][0]:
                    table[ch] = (cost, stroke)
        self._built = (
            {ch: stroke for ch, (_, stroke) in best.items()},
            {ch: stroke for ch, (_, stroke) in best_dead.items()},
        )
        return self._built


class XkbState:
    """Tracks modifier state and translates key presses into text."""

    def __init__(self, keymap: XkbKeymap) -> None:
        self._state = _xkb().xkb_state_new(keymap._keymap)
        if not self._state:
            raise XkbError("xkb_state_new failed")

    def close(self) -> None:
        if self._state:
            _xkb().xkb_state_unref(self._state)
            self._state = None

    def update(self, code: int, down: bool) -> None:
        _xkb().xkb_state_update_key(self._state, code + _EVDEV_OFFSET, _XKB_KEY_DOWN if down else _XKB_KEY_UP)

    def mods(self) -> int:
        return _xkb().xkb_state_serialize_mods(self._state, _XKB_STATE_MODS_EFFECTIVE)

    def utf8(self, code: int) -> str:
        """Text the key produces in the current modifier state ('' for modifiers/dead keys/etc.)."""
        buf = ctypes.create_string_buffer(32)
        n = _xkb().xkb_state_key_get_utf8(self._state, code + _EVDEV_OFFSET, buf, 32)
        return buf.raw[:n].decode("utf-8", "replace") if 0 < n < 32 else ""

    def mod_active(self, name: str) -> bool:
        return _xkb().xkb_state_mod_name_is_active(self._state, name.encode(), _XKB_STATE_MODS_EFFECTIVE) > 0
