"""``keyflow`` command line: manage shortcuts and settings, run and diagnose the daemon."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Optional

from . import __version__, importers
from .core import (
    EXPANSION_KEYS,
    AppSettings,
    ExpansionEngine,
    Shortcut,
    adapt_case,
    process_placeholders,
)
from .store import LINUX_FIELDS, Store, _SETTINGS_FIELDS, LinuxConfig


def _err(msg: str) -> int:
    print(f"keyflow: {msg}", file=sys.stderr)
    return 1


def _read_text_arg(value: str) -> str:
    return sys.stdin.read() if value == "-" else value


def _one_line(text: str, width: int = 60) -> str:
    flat = text.replace("\n", "⏎")
    return flat if len(flat) <= width else flat[: width - 1] + "…"


# -- shortcuts ------------------------------------------------------------------------------


def cmd_list(args: argparse.Namespace, store: Store) -> int:
    shortcuts = store.load_shortcuts()
    if args.json:
        sys.stdout.write(importers.to_json(shortcuts))
        return 0
    if not shortcuts:
        print("No shortcuts yet. Add one with: keyflow add ';addr' '123 Main St'")
        return 0
    width = max(len(s.trigger) for s in shortcuts)
    for s in sorted(shortcuts, key=lambda s: (s.folder or "", s.trigger.lower())):
        flag = " " if s.enabled else "x"
        folder = f"[{s.folder}] " if s.folder else ""
        print(f"{flag} {s.trigger.ljust(width)}  {folder}{_one_line(s.expansion)}")
    print(f"\n{len(shortcuts)} shortcut(s); 'x' marks disabled ones.", file=sys.stderr)
    return 0


def cmd_add(args: argparse.Namespace, store: Store) -> int:
    trigger = args.trigger.strip(" \t")
    expansion = _read_text_arg(args.expansion)
    if not trigger:
        return _err("trigger must not be empty")
    if not expansion:
        return _err("expansion must not be empty")
    shortcuts = store.load_shortcuts()
    existing = store.find(shortcuts, trigger)
    if existing and not args.force:
        return _err(f"'{existing.trigger}' already exists (use --force to overwrite)")
    new = Shortcut(trigger, expansion, False, args.group, not args.disabled, args.folder)
    shortcuts = [s for s in shortcuts if s is not existing] + [new]
    store.save_shortcuts(shortcuts)
    print(f"{'Updated' if existing else 'Added'} {trigger}")
    return 0


def _modify(args: argparse.Namespace, store: Store, action: str) -> int:
    shortcuts = store.load_shortcuts()
    target = store.find(shortcuts, args.trigger)
    if target is None:
        return _err(f"no shortcut with trigger '{args.trigger}'")
    if action == "remove":
        shortcuts.remove(target)
    else:
        shortcuts[shortcuts.index(target)] = replace(target, enabled=(action == "enable"))
    store.save_shortcuts(shortcuts)
    verb = {"remove": "Removed", "enable": "Enabled", "disable": "Disabled"}[action]
    print(f"{verb} {target.trigger}")
    return 0


def cmd_import(args: argparse.Namespace, store: Store) -> int:
    path = Path(args.file)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return _err(f"cannot read {path}: {e}")
    fmt = args.format or ("csv" if path.suffix.lower() == ".csv" else "json")
    parsed = importers.parse_csv(text) if fmt == "csv" else importers.parse_json(text)

    shortcuts = store.load_shortcuts()
    resolved = importers.resolve_import(
        parsed, lambda t: store.find(shortcuts, t), overwrite_duplicates=args.overwrite
    )
    for s in resolved.to_save:
        existing = store.find(shortcuts, s.trigger)
        if existing:
            shortcuts.remove(existing)
        shortcuts.append(s)
    store.save_shortcuts(shortcuts)

    print(f"Imported {len(resolved.to_save)} shortcut(s), skipped {len(resolved.skipped)}.")
    if resolved.skipped:
        for row in resolved.skipped[:20]:
            print(f"  row {row.row_number}: {row.reason} ({_one_line(row.trigger or row.raw_row, 40)})")
        if len(resolved.skipped) > 20:
            print(f"  … and {len(resolved.skipped) - 20} more")
        if args.skipped_out:
            Path(args.skipped_out).write_text(importers.skipped_to_csv(resolved.skipped), encoding="utf-8")
            print(f"Skipped rows written to {args.skipped_out} (fix and re-import).")
    return 0


def cmd_export(args: argparse.Namespace, store: Store) -> int:
    shortcuts = store.load_shortcuts()
    fmt = args.format or ("csv" if (args.output or "").lower().endswith(".csv") else "json")
    out = importers.to_csv(shortcuts) if fmt == "csv" else importers.to_json(shortcuts)
    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
        print(f"Exported {len(shortcuts)} shortcut(s) to {args.output}")
    else:
        sys.stdout.write(out)
    return 0


# -- settings -------------------------------------------------------------------------------


def _settings_view(settings: AppSettings, linux: LinuxConfig) -> dict[str, object]:
    view: dict[str, object] = {k: getattr(settings, attr) for k, (attr, _) in _SETTINGS_FIELDS.items()}
    view.update({k: getattr(linux, attr) for k, (attr, _) in LINUX_FIELDS.items()})
    return view


def _parse_bool(value: str) -> bool:
    v = value.strip().lower()
    if v in ("true", "1", "yes", "on"):
        return True
    if v in ("false", "0", "no", "off"):
        return False
    raise ValueError(f"expected true/false, got '{value}'")


def cmd_settings(args: argparse.Namespace, store: Store) -> int:
    settings, linux = store.load_settings()
    if args.key is None:
        for k, v in _settings_view(settings, linux).items():
            print(f"{k} = {v!r}")
        return 0

    key = args.key
    if args.value is None:
        view = _settings_view(settings, linux)
        if key not in view:
            return _err(f"unknown setting '{key}'. Known: {', '.join(view)}")
        print(view[key])
        return 0

    try:
        if key in _SETTINGS_FIELDS:
            attr, typ = _SETTINGS_FIELDS[key]
            value = _parse_bool(args.value) if typ is bool else args.value
            if key == "expansionKey" and value not in EXPANSION_KEYS:
                raise ValueError(f"expansionKey must be one of: {', '.join(EXPANSION_KEYS)}")
            setattr(settings, attr, value)
        elif key in LINUX_FIELDS:
            attr, typ = LINUX_FIELDS[key]
            if typ is list:
                value = [v for v in args.value.split(",") if v]
            elif typ is float:
                value = float(args.value)
                if not 0 <= value <= 100:
                    raise ValueError("injectDelayMs must be between 0 and 100")
            else:
                value = args.value
            setattr(linux, attr, value)
        else:
            return _err(f"unknown setting '{key}'. Known: {', '.join(_settings_view(settings, linux))}")
    except ValueError as e:
        return _err(str(e))
    store.save_settings(settings, linux)
    print(f"{key} = {args.value}")
    return 0


def cmd_pause_resume(args: argparse.Namespace, store: Store, enabled: bool) -> int:
    settings, linux = store.load_settings()
    settings.service_enabled = enabled
    store.save_settings(settings, linux)
    print("Expansion resumed." if enabled else "Expansion paused (the daemon keeps running).")
    return 0


# -- try ------------------------------------------------------------------------------------


def cmd_try(args: argparse.Namespace, store: Store) -> int:
    """Dry run: what would typing this text expand to? Nothing is typed anywhere."""
    settings, _ = store.load_settings()
    engine = ExpansionEngine()
    engine.update_shortcuts(store.load_shortcuts())
    typed = _read_text_arg(args.text)
    for end in range(1, len(typed) + 1):
        match = engine.find_match(typed[:end], settings)
        if match is None:
            continue
        processed = process_placeholders(match.shortcut.expansion)
        text = processed.text
        if not settings.case_sensitive and settings.preserve_case:
            text = adapt_case(match.typed_trigger, text)
        print(f"'{match.typed_trigger}' (after {end} typed characters) would become:")
        print(text)
        if processed.cursor_offset is not None:
            print(f"[cursor ends {len(text) - processed.cursor_offset} character(s) from the end]")
        return 0
    print("No expansion would fire.")
    print(f"(expansionKey={settings.expansion_key}: "
          + ("type a space or punctuation after the trigger." if settings.expansion_key == "boundaryKey"
             else "fires as soon as the trigger is typed.") + ")")
    return 2


# -- doctor ---------------------------------------------------------------------------------


def cmd_doctor(args: argparse.Namespace, store: Store) -> int:
    problems = 0

    def report(ok: Optional[bool], label: str, detail: str = "") -> None:
        nonlocal problems
        mark = {True: "ok  ", False: "FAIL", None: "info"}[ok]
        if ok is False:
            problems += 1
        print(f"[{mark}] {label}" + (f": {detail}" if detail else ""))

    session = os.environ.get("XDG_SESSION_TYPE", "unknown")
    report(None, "session type", f"{session} (both X11 and Wayland are supported)")

    try:
        import evdev  # noqa: F401
        report(True, "python-evdev")
    except ImportError:
        report(False, "python-evdev", "missing; install with: sudo pacman -S python-evdev")

    from .layout import detect_layout
    from .xkb import XkbError, XkbKeymap

    settings, linux = store.load_settings()
    names, source = detect_layout(linux)
    try:
        km = XkbKeymap(names)
        chars = len(km.reverse_map())
        km.close()
        report(True, "libxkbcommon + keyboard layout", f"{names.describe()} (from {source}); {chars} typeable characters")
        if source.startswith("default"):
            report(False, "layout detection", "nothing detected, assuming 'us'. Set it with: "
                   "keyflow settings keyboard.layout fr")
    except XkbError as e:
        report(False, "libxkbcommon / keyboard layout", str(e))

    try:
        from .devices import scan_devices

        scan = scan_devices(set(), linux.input_devices)
        for dev in list(scan.keyboards.values()) + list(scan.pointers.values()):
            dev.close()
        if scan.keyboards:
            report(True, "keyboards readable", ", ".join(d.name for d in scan.keyboards.values()))
        elif scan.denied:
            report(False, "keyboards readable", f"{scan.denied} input device(s) not accessible; "
                   "run: sudo usermod -aG input $USER  (then log out and in)")
        else:
            report(False, "keyboards readable", "no keyboard found")
    except ImportError:
        pass

    uinput = Path("/dev/uinput")
    if not uinput.exists():
        report(False, "/dev/uinput", "missing; run: sudo modprobe uinput")
    elif os.access(uinput, os.W_OK):
        report(True, "/dev/uinput writable")
    else:
        report(False, "/dev/uinput", "not writable; install the udev rule from the package "
               "(group 'input') and re-login")

    report(None, "config directory", str(store.directory))
    try:
        report(True, "shortcuts", f"{len(store.load_shortcuts())} loaded")
    except ValueError as e:
        report(False, "shortcuts", str(e))
    report(None, "service", "enabled" if settings.service_enabled else "PAUSED (keyflow resume)")

    from .inject import Clipboard
    cb = Clipboard()
    report(None, "clipboard fallback (for characters not on your layout)",
           "available" if cb.available else "unavailable (install wl-clipboard or xclip)")
    return 1 if problems else 0


# -- entry point ----------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="keyflow", description="System-wide text expander for Linux.")
    p.add_argument("--version", action="version", version=f"keyflow {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("daemon", help="run the expansion service (normally via systemd)")

    s = sub.add_parser("list", help="list shortcuts")
    s.add_argument("--json", action="store_true", help="print the JSON export format")

    s = sub.add_parser("add", help="add a shortcut")
    s.add_argument("trigger")
    s.add_argument("expansion", help="text to insert, or '-' to read it from stdin")
    s.add_argument("--group")
    s.add_argument("--folder")
    s.add_argument("--disabled", action="store_true")
    s.add_argument("--force", action="store_true", help="overwrite an existing trigger")

    for name, hlp in (("remove", "delete a shortcut"), ("enable", "enable a shortcut"),
                      ("disable", "disable a shortcut")):
        s = sub.add_parser(name, help=hlp)
        s.add_argument("trigger")

    s = sub.add_parser("import", help="import shortcuts from a JSON or CSV file")
    s.add_argument("file")
    s.add_argument("--format", choices=("json", "csv"))
    s.add_argument("--overwrite", action="store_true", help="replace shortcuts with the same trigger")
    s.add_argument("--skipped-out", metavar="FILE", help="write skipped rows to this CSV")

    s = sub.add_parser("export", help="export shortcuts as JSON or CSV")
    s.add_argument("-o", "--output")
    s.add_argument("--format", choices=("json", "csv"))

    s = sub.add_parser("settings", help="show or change a setting")
    s.add_argument("key", nargs="?")
    s.add_argument("value", nargs="?")

    sub.add_parser("pause", help="stop expanding without stopping the service")
    sub.add_parser("resume", help="resume expanding")

    s = sub.add_parser("try", help="dry run: show what typing TEXT would expand to")
    s.add_argument("text", help="typed text, e.g. ';addr ' (or '-' for stdin)")

    sub.add_parser("doctor", help="check permissions, layout and dependencies")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "daemon":
        from .daemon import main as daemon_main

        return daemon_main()

    store = Store()
    try:
        if args.command == "list":
            return cmd_list(args, store)
        if args.command == "add":
            return cmd_add(args, store)
        if args.command in ("remove", "enable", "disable"):
            return _modify(args, store, args.command)
        if args.command == "import":
            return cmd_import(args, store)
        if args.command == "export":
            return cmd_export(args, store)
        if args.command == "settings":
            return cmd_settings(args, store)
        if args.command in ("pause", "resume"):
            return cmd_pause_resume(args, store, args.command == "resume")
        if args.command == "try":
            return cmd_try(args, store)
        if args.command == "doctor":
            return cmd_doctor(args, store)
    except ValueError as e:  # unreadable config files
        return _err(str(e))
    return 0
