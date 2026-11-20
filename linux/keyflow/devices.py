"""Input device discovery (/dev/input/event*) via python-evdev."""

from __future__ import annotations

import errno
import glob
import logging
import os
from dataclasses import dataclass
from typing import Any

from . import keys
from .inject import VIRTUAL_DEVICE_NAME

log = logging.getLogger("keyflow")

_KEYBOARD_KEYS = {30, 44, 28, keys.KEY_SPACE}  # A, Z, Enter, Space


@dataclass
class Scan:
    keyboards: dict[str, Any]  # path -> InputDevice (opened, non-blocking)
    pointers: dict[str, Any]
    denied: int  # event nodes we could not open


def _wanted(dev: Any, filters: list[str]) -> bool:
    if not filters:
        return True
    return any(f in dev.name or f in dev.path for f in filters)


def scan_devices(known: set[str], filters: list[str], input_dir: str = "/dev/input") -> Scan:
    """Open every keyboard / pointer that is not already in ``known``.

    Event nodes are globbed here instead of using ``evdev.list_devices()``, which silently drops
    nodes the user cannot read *and write*: that hid the real problem (not in the ``input``
    group) behind a misleading "no keyboard found". Only read access is needed.
    """
    from evdev import InputDevice, ecodes

    keyboards: dict[str, Any] = {}
    pointers: dict[str, Any] = {}
    denied = 0
    for path in sorted(glob.glob(os.path.join(input_dir, "event*"))):
        if path in known:
            continue
        if not os.access(path, os.R_OK):
            denied += 1
            continue
        try:
            dev = InputDevice(path)
        except PermissionError:
            denied += 1
            continue
        except OSError as e:
            if e.errno != errno.ENODEV:
                log.debug("cannot open %s: %s", path, e)
            continue

        keep = False
        try:
            if dev.name != VIRTUAL_DEVICE_NAME:
                codes = set(dev.capabilities().get(ecodes.EV_KEY, []))
                if _KEYBOARD_KEYS <= codes and _wanted(dev, filters):
                    os.set_blocking(dev.fd, False)
                    keyboards[path] = dev
                    keep = True
                elif codes & keys.POINTER_BUTTONS:
                    os.set_blocking(dev.fd, False)
                    pointers[path] = dev
                    keep = True
        except OSError:
            pass
        if not keep:
            dev.close()
    return Scan(keyboards, pointers, denied)
