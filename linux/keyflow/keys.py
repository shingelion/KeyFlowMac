"""Linux input-event key codes used by KeyFlow (from <linux/input-event-codes.h>)."""

KEY_ESC = 1
KEY_BACKSPACE = 14
KEY_TAB = 15
KEY_ENTER = 28
KEY_LEFTCTRL = 29
KEY_LEFTSHIFT = 42
KEY_RIGHTSHIFT = 54
KEY_LEFTALT = 56
KEY_SPACE = 57
KEY_CAPSLOCK = 58
KEY_NUMLOCK = 69
KEY_SCROLLLOCK = 70
KEY_KPENTER = 96
KEY_RIGHTCTRL = 97
KEY_RIGHTALT = 100
KEY_LEFT = 105
KEY_INSERT = 110
KEY_LEFTMETA = 125
KEY_RIGHTMETA = 126

BTN_LEFT = 0x110
BTN_RIGHT = 0x111
BTN_MIDDLE = 0x112
BTN_TOUCH = 0x14A

#: Pressing any of these means a shortcut / launcher / window switch is likely under way, so the
#: typed-text buffer can no longer be trusted.
CLEARING_MODIFIERS = frozenset({KEY_LEFTCTRL, KEY_RIGHTCTRL, KEY_LEFTALT, KEY_LEFTMETA, KEY_RIGHTMETA})

#: Modifiers / locks that never produce text and do not disturb the buffer.
NEUTRAL_MODIFIERS = frozenset(
    {KEY_LEFTSHIFT, KEY_RIGHTSHIFT, KEY_CAPSLOCK, KEY_NUMLOCK, KEY_SCROLLLOCK, KEY_RIGHTALT}
)

POINTER_BUTTONS = frozenset({BTN_LEFT, BTN_RIGHT, BTN_MIDDLE, BTN_TOUCH})
