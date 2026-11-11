"""On-disk configuration: ``~/.config/keyflow/{shortcuts,settings}.json``.

The daemon and the CLI never talk to each other directly. The CLI edits these files (atomically)
and the daemon notices the new modification time and reloads, so there is no IPC to secure.
``shortcuts.json`` uses the same schema as the JSON export of the macOS/Android apps.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import importers
from .core import EXPANSION_KEYS, AppSettings, Shortcut

# camelCase on disk, identical to the Swift Codable field names.
_SETTINGS_FIELDS = {
    "serviceEnabled": ("service_enabled", bool),
    "caseSensitive": ("case_sensitive", bool),
    "preserveCase": ("preserve_case", bool),
    "expansionKey": ("expansion_key", str),
    "triggerPrefix": ("trigger_prefix", str),
}


@dataclass
class LinuxConfig:
    """Settings that only make sense on Linux."""

    #: XKB layout override. Empty = auto-detect (see ``layout.detect_layout``).
    xkb_layout: str = ""
    xkb_variant: str = ""
    xkb_model: str = ""
    xkb_options: str = ""
    #: If non-empty, only read keyboards whose name or /dev path contains one of these strings.
    input_devices: list[str] = field(default_factory=list)
    #: Pause between synthetic key events, in milliseconds.
    inject_delay_ms: float = 2.0


LINUX_FIELDS = {
    "keyboard.layout": ("xkb_layout", str),
    "keyboard.variant": ("xkb_variant", str),
    "keyboard.model": ("xkb_model", str),
    "keyboard.options": ("xkb_options", str),
    "inputDevices": ("input_devices", list),
    "injectDelayMs": ("inject_delay_ms", float),
}


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "keyflow"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, 0o600)  # snippets can contain addresses, signatures, secrets...
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class Store:
    def __init__(self, directory: Optional[Path] = None) -> None:
        self.directory = directory or config_dir()
        self.shortcuts_path = self.directory / "shortcuts.json"
        self.settings_path = self.directory / "settings.json"

    # -- change detection -------------------------------------------------------------------

    def fingerprint(self) -> tuple:
        def stat(p: Path):
            try:
                s = p.stat()
                return (s.st_mtime_ns, s.st_size)
            except OSError:
                return None

        return (stat(self.shortcuts_path), stat(self.settings_path))

    # -- shortcuts --------------------------------------------------------------------------

    def load_shortcuts(self) -> list[Shortcut]:
        """Raises ValueError if the file exists but is not usable (so callers can keep old data)."""
        try:
            text = self.shortcuts_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        result = importers.parse_json(text)
        if not result.shortcuts and result.skipped and result.skipped[0].row_number == 0:
            raise ValueError(f"{self.shortcuts_path}: {result.skipped[0].reason}")
        return [i.shortcut for i in result.shortcuts]

    def save_shortcuts(self, shortcuts: list[Shortcut]) -> None:
        _atomic_write(self.shortcuts_path, importers.to_json(shortcuts))

    def find(self, shortcuts: list[Shortcut], trigger: str) -> Optional[Shortcut]:
        wanted = trigger.lower()
        return next((s for s in shortcuts if s.trigger.lower() == wanted), None)

    # -- settings ---------------------------------------------------------------------------

    def _read_settings_dict(self) -> dict[str, Any]:
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        if not isinstance(data, dict):
            raise ValueError(f"{self.settings_path}: expected a JSON object")
        return data

    def load_settings(self) -> tuple[AppSettings, LinuxConfig]:
        """Unknown/invalid values fall back to defaults; raises ValueError only on bad JSON."""
        try:
            data = self._read_settings_dict()
        except json.JSONDecodeError as e:
            raise ValueError(f"{self.settings_path}: {e}") from e

        settings = AppSettings()
        for key, (attr, typ) in _SETTINGS_FIELDS.items():
            if key in data and isinstance(data[key], typ):
                setattr(settings, attr, data[key])
        if settings.expansion_key not in EXPANSION_KEYS:
            settings.expansion_key = AppSettings().expansion_key

        linux = LinuxConfig()
        kb = data.get("keyboard") if isinstance(data.get("keyboard"), dict) else {}
        for name in ("layout", "variant", "model", "options"):
            if isinstance(kb.get(name), str):
                setattr(linux, f"xkb_{name}", kb[name])
        devs = data.get("inputDevices")
        if isinstance(devs, list):
            linux.input_devices = [d for d in devs if isinstance(d, str)]
        delay = data.get("injectDelayMs")
        if isinstance(delay, (int, float)) and not isinstance(delay, bool) and 0 <= delay <= 100:
            linux.inject_delay_ms = float(delay)
        return settings, linux

    def save_settings(self, settings: AppSettings, linux: LinuxConfig) -> None:
        data: dict[str, Any] = {
            key: getattr(settings, attr) for key, (attr, _) in _SETTINGS_FIELDS.items()
        }
        data["keyboard"] = {
            "layout": linux.xkb_layout,
            "variant": linux.xkb_variant,
            "model": linux.xkb_model,
            "options": linux.xkb_options,
        }
        data["inputDevices"] = linux.input_devices
        data["injectDelayMs"] = linux.inject_delay_ms
        _atomic_write(self.settings_path, json.dumps(data, indent=2, sort_keys=True) + "\n")
