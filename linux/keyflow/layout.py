"""Work out which XKB keyboard layout the desktop is using.

The kernel does not know the layout, but the characters we recognise and type depend on it, so a
wrong guess means garbage output. Sources are tried from the most to the least specific; the
first that yields a layout wins. ``settings.json`` -> ``keyboard`` always overrides detection.
"""

from __future__ import annotations

import configparser
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from .store import LinuxConfig
from .xkb import KeymapNames


def _run(cmd: list[str]) -> Optional[str]:
    if not shutil.which(cmd[0]):
        return None
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def _from_env() -> Optional[KeymapNames]:
    layout = os.environ.get("XKB_DEFAULT_LAYOUT")
    if not layout:
        return None
    return KeymapNames(
        layout=layout,
        variant=os.environ.get("XKB_DEFAULT_VARIANT", ""),
        model=os.environ.get("XKB_DEFAULT_MODEL", ""),
        options=os.environ.get("XKB_DEFAULT_OPTIONS", ""),
    )


def _from_gsettings() -> Optional[KeymapNames]:
    out = _run(["gsettings", "get", "org.gnome.desktop.input-sources", "sources"])
    m = re.search(r"\('xkb',\s*'([^']+)'\)", out or "")
    if not m:
        return None
    layout, _, variant = m.group(1).partition("+")
    opts = _run(["gsettings", "get", "org.gnome.desktop.input-sources", "xkb-options"]) or ""
    options = ",".join(re.findall(r"'([^']+)'", opts))
    return KeymapNames(layout=layout, variant=variant, options=options)


def _from_kde() -> Optional[KeymapNames]:
    cfg = configparser.ConfigParser(interpolation=None)
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    try:
        cfg.read(Path(base) / "kxkbrc", encoding="utf-8")
        sect = cfg["Layout"]
        layouts = sect.get("LayoutList", "")
        if not layouts or sect.get("Use", "false") != "true":
            return None
        return KeymapNames(
            layout=layouts,
            variant=sect.get("VariantList", ""),
            options=sect.get("Options", "") if sect.get("ResetOldOptions", "false") == "true" else "",
        )
    except (OSError, KeyError, configparser.Error):
        return None


def _parse_key_values(text: str, sep: str) -> dict[str, str]:
    result = {}
    for line in text.splitlines():
        key, s, value = line.partition(sep)
        if s:
            result[key.strip()] = value.strip()
    return result


def _from_setxkbmap() -> Optional[KeymapNames]:
    if not os.environ.get("DISPLAY"):
        return None
    out = _run(["setxkbmap", "-query"])
    if not out:
        return None
    d = _parse_key_values(out, ":")
    if not d.get("layout"):
        return None
    return KeymapNames(layout=d["layout"], variant=d.get("variant", ""), model=d.get("model", ""),
                       options=d.get("options", ""))


def _from_localectl() -> Optional[KeymapNames]:
    out = _run(["localectl", "status"])
    if not out:
        return None
    d = _parse_key_values(out, ":")
    layout = d.get("X11 Layout", "")
    if not layout or layout == "(unset)":
        return None

    def val(key: str) -> str:
        v = d.get(key, "")
        return "" if v == "(unset)" else v

    return KeymapNames(layout=layout, variant=val("X11 Variant"), model=val("X11 Model"),
                       options=val("X11 Options"))


_DETECTORS = (
    ("environment (XKB_DEFAULT_*)", _from_env),
    ("GNOME settings", _from_gsettings),
    ("KDE settings", _from_kde),
    ("setxkbmap", _from_setxkbmap),
    ("localectl", _from_localectl),
)


def detect_layout(config: LinuxConfig) -> tuple[KeymapNames, str]:
    """Returns (names, human readable source)."""
    if config.xkb_layout:
        return (
            KeymapNames(config.xkb_layout, config.xkb_variant, config.xkb_model, config.xkb_options),
            "settings.json",
        )
    for source, fn in _DETECTORS:
        names = fn()
        if names:
            return names, source
    return KeymapNames("us"), "default (nothing detected)"
