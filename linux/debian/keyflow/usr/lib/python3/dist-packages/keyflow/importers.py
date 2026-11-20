"""JSON / CSV import & export — port of ``Sources/KeyFlowCore/Importer``.

Uses the exact same schema as the macOS and Android apps, so an export from one platform imports
cleanly on the others. (No aText importer, as on macOS.)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Callable, Optional

from .core import Shortcut

_WS = " \t"  # Swift's CharacterSet.whitespaces: spaces and tabs, not newlines.


@dataclass(frozen=True)
class ImportedShortcut:
    shortcut: Shortcut
    folder_name: Optional[str]
    row_number: int
    raw_row: str


@dataclass(frozen=True)
class SkippedRow:
    row_number: int
    trigger: str
    expansion: str
    group: str
    folder: str
    enabled: str
    raw_row: str
    reason: str


@dataclass
class ImportResult:
    shortcuts: list[ImportedShortcut] = field(default_factory=list)
    skipped: list[SkippedRow] = field(default_factory=list)


@dataclass
class ResolvedImport:
    to_save: list[Shortcut]
    skipped: list[SkippedRow]


def _file_level_failure(raw: str, reason: str) -> SkippedRow:
    return SkippedRow(0, "", "", "", "", "", raw[:500], reason)


def _swift_bool(value: bool) -> str:
    return "true" if value else "false"


# --------------------------------------------------------------------------------------------
# JSON
# --------------------------------------------------------------------------------------------


def parse_json(text: str) -> ImportResult:
    try:
        parsed = json.loads(text)
    except ValueError as e:
        return ImportResult([], [_file_level_failure(text, f"Invalid JSON: {e}")])

    if isinstance(parsed, list):
        array = parsed
    elif isinstance(parsed, dict) and isinstance(parsed.get("shortcuts"), list):
        array = parsed["shortcuts"]
    else:
        return ImportResult([], [_file_level_failure(text, 'No "shortcuts" array found in JSON.')])

    shortcuts: list[ImportedShortcut] = []
    skipped: list[SkippedRow] = []

    for index, item in enumerate(array):
        row_number = index + 1
        if not isinstance(item, dict):
            skipped.append(SkippedRow(row_number, "", "", "", "", "", str(item), "Malformed row"))
            continue

        raw_row = json.dumps(item, ensure_ascii=False, sort_keys=True)
        trigger = _str(item.get("trigger")).strip(_WS)
        expansion = _str(item.get("expansion"))
        group = _str(item.get("group")).strip(_WS)
        folder = _str(item.get("folder")).strip(_WS)
        enabled = item.get("enabled") if isinstance(item.get("enabled"), bool) else True

        reason = None
        if not trigger:
            reason = "Empty trigger"
        elif not expansion:
            reason = "Empty expansion"
        if reason:
            skipped.append(
                SkippedRow(row_number, trigger, expansion, group, folder, _swift_bool(enabled), raw_row, reason)
            )
            continue

        is_rich = item.get("isRichText") if isinstance(item.get("isRichText"), bool) else False
        shortcuts.append(
            ImportedShortcut(
                Shortcut(trigger, expansion, is_rich, group or None, enabled, folder or None),
                folder or None,
                row_number,
                raw_row,
            )
        )
    return ImportResult(shortcuts, skipped)


def _str(value: object) -> str:
    return value if isinstance(value, str) else ""


def to_json(shortcuts: list[Shortcut]) -> str:
    array = [
        {
            "trigger": s.trigger,
            "expansion": s.expansion,
            "isRichText": s.is_rich_text,
            "group": s.group,
            "folder": s.folder,
            "enabled": s.enabled,
        }
        for s in shortcuts
    ]
    return json.dumps({"shortcuts": array}, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


# --------------------------------------------------------------------------------------------
# CSV
# --------------------------------------------------------------------------------------------


def parse_csv_rows(content: str) -> list[list[str]]:
    """Minimal RFC-4180-ish parser: quoted fields, ``""`` escapes, commas/newlines in quotes."""
    rows: list[list[str]] = []
    row: list[str] = []
    field_chars: list[str] = []
    in_quotes = False
    text = content.replace("\r\n", "\n").replace("\r", "\n")
    i, n = 0, len(text)

    def end_field() -> None:
        row.append("".join(field_chars))
        field_chars.clear()

    def end_row() -> None:
        nonlocal row
        end_field()
        rows.append(row)
        row = []

    while i < n:
        c = text[i]
        if in_quotes:
            if c == '"':
                if i + 1 < n and text[i + 1] == '"':
                    field_chars.append('"')
                    i += 1
                else:
                    in_quotes = False
            else:
                field_chars.append(c)
        elif c == '"':
            in_quotes = True
        elif c == ",":
            end_field()
        elif c == "\n":
            end_row()
        else:
            field_chars.append(c)
        i += 1
    if field_chars or row:
        end_row()
    return [r for r in rows if any(f.strip(_WS) for f in r)]


def csv_field(value: str) -> str:
    if "," in value or '"' in value or "\n" in value:
        return '"' + value.replace('"', '""') + '"'
    return value


def parse_csv(content: str) -> ImportResult:
    rows = parse_csv_rows(content)
    if not rows:
        return ImportResult([], [SkippedRow(0, "", "", "", "", "", "", "File is empty.")])

    start = 0
    if rows[0] and rows[0][0].strip(_WS).lower() == "trigger":
        start = 1

    shortcuts: list[ImportedShortcut] = []
    skipped: list[SkippedRow] = []

    for i in range(start, len(rows)):
        row = rows[i]
        row_number = i + 1
        raw_row = ",".join(csv_field(f) for f in row)

        if len(row) < 2:
            skipped.append(
                SkippedRow(row_number, row[0].strip(_WS) if row else "", "", "", "", "", raw_row, "Malformed CSV row")
            )
            continue

        trigger = row[0].strip(_WS)
        expansion = row[1]
        group = row[2].strip(_WS) if len(row) > 2 else ""
        enabled_raw = row[3].strip(_WS) if len(row) > 3 else ""
        folder = row[4].strip(_WS) if len(row) > 4 else ""

        reason = None
        if not trigger:
            reason = "Empty trigger"
        elif not expansion:
            reason = "Empty expansion"
        if reason:
            skipped.append(SkippedRow(row_number, trigger, expansion, group, folder, enabled_raw, raw_row, reason))
            continue

        enabled = True if not enabled_raw else (enabled_raw.lower() == "true" or enabled_raw == "1")
        shortcuts.append(
            ImportedShortcut(
                Shortcut(trigger, expansion, False, group or None, enabled, folder or None),
                folder or None,
                row_number,
                raw_row,
            )
        )
    return ImportResult(shortcuts, skipped)


def to_csv(shortcuts: list[Shortcut]) -> str:
    lines = ["trigger,expansion,group,enabled,folder"]
    for s in shortcuts:
        lines.append(
            ",".join(
                [
                    csv_field(s.trigger),
                    csv_field(s.expansion),
                    csv_field(s.group or ""),
                    _swift_bool(s.enabled),
                    csv_field(s.folder or ""),
                ]
            )
        )
    return "\n".join(lines) + "\n"


def skipped_to_csv(skipped: list[SkippedRow]) -> str:
    """Skipped rows in the importer's own columns plus ``reason`` so they can be fixed and re-imported."""
    lines = ["trigger,expansion,group,enabled,folder,reason"]
    for r in skipped:
        lines.append(",".join(csv_field(f) for f in (r.trigger, r.expansion, r.group, r.enabled, r.folder, r.reason)))
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------------------------
# Resolver
# --------------------------------------------------------------------------------------------


def resolve_import(
    result: ImportResult,
    get_existing_by_trigger: Callable[[str], Optional[Shortcut]],
    overwrite_duplicates: bool,
) -> ResolvedImport:
    """Catch duplicate triggers within the file and against already-saved shortcuts.

    Triggers are compared case-insensitively, because the expansion engine treats them that way.
    """
    seen: set[str] = set()
    to_save: list[Shortcut] = []
    new_skips: list[SkippedRow] = []

    for item in result.shortcuts:
        key = item.shortcut.trigger.lower()
        duplicate_in_batch = key in seen
        seen.add(key)
        existing = None if duplicate_in_batch else get_existing_by_trigger(item.shortcut.trigger)

        if duplicate_in_batch or (existing is not None and not overwrite_duplicates):
            s = item.shortcut
            new_skips.append(
                SkippedRow(
                    item.row_number, s.trigger, s.expansion, s.group or "", item.folder_name or "",
                    _swift_bool(s.enabled), item.raw_row, "Duplicate trigger",
                )
            )
            continue
        to_save.append(replace(item.shortcut, folder=item.folder_name))

    return ResolvedImport(to_save, result.skipped + new_skips)
