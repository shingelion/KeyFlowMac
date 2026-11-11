"""Platform-agnostic core: models, trigger matching, case adaptation and placeholders.

Direct port of ``Sources/KeyFlowCore`` (ExpansionEngine, CaseAdapter, PlaceholderProcessor,
Shortcut, AppSettings). Behaviour is kept identical so that the same shortcut list expands the
same way on macOS, Android and Linux.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass
from typing import Optional

EXPANSION_IMMEDIATE = "immediate"
EXPANSION_BOUNDARY = "boundaryKey"
EXPANSION_KEYS = (EXPANSION_IMMEDIATE, EXPANSION_BOUNDARY)


@dataclass
class Shortcut:
    trigger: str
    expansion: str
    is_rich_text: bool = False
    group: Optional[str] = None
    enabled: bool = True
    #: Folder *name*, or None for "Uncategorized". (macOS stores a folder id; names are the
    #: portable representation used by the JSON/CSV exchange format.)
    folder: Optional[str] = None


@dataclass
class AppSettings:
    service_enabled: bool = True
    case_sensitive: bool = False
    preserve_case: bool = True
    expansion_key: str = EXPANSION_BOUNDARY
    trigger_prefix: str = ""


@dataclass(frozen=True)
class ExpansionMatch:
    shortcut: Shortcut
    #: Start offset (in characters) of the trigger within the text, inclusive.
    match_start: int
    #: End offset (in characters) of the trigger within the text, exclusive.
    match_end: int
    #: The exact substring the user typed (used to preserve/adapt case).
    typed_trigger: str


def _is_boundary_char(c: str) -> bool:
    if c in (" ", "\t", "\n"):
        return True
    return c in ".,;:!?)]}\"'"


class ExpansionEngine:
    """In-memory trigger matcher (see ExpansionEngine.swift)."""

    def __init__(self) -> None:
        self._triggers_by_length_desc: list[str] = []
        self._shortcuts_by_lower_trigger: dict[str, Shortcut] = {}

    def update_shortcuts(self, shortcuts: list[Shortcut]) -> None:
        enabled = [s for s in shortcuts if s.enabled]
        # Later entries win on case-insensitive collisions, as in the Swift version.
        self._shortcuts_by_lower_trigger = {s.trigger.lower(): s for s in enabled}
        self._triggers_by_length_desc = sorted(
            {s.trigger for s in enabled}, key=lambda t: (-len(t), t)
        )

    def find_match(self, text_before_cursor: str, settings: AppSettings) -> Optional[ExpansionMatch]:
        if not self._triggers_by_length_desc or not text_before_cursor:
            return None

        chars = text_before_cursor
        if settings.expansion_key == EXPANSION_BOUNDARY:
            if not _is_boundary_char(chars[-1]):
                return None
            search = chars[:-1]
        else:
            search = chars
        if not search:
            return None

        for trigger in self._triggers_by_length_desc:
            if len(search) < len(trigger):
                continue
            start = len(search) - len(trigger)
            candidate = search[start:]

            if settings.case_sensitive:
                matches = candidate == trigger
            else:
                matches = candidate.lower() == trigger.lower()
            if not matches:
                continue

            preceding = search[start - 1] if start > 0 else None
            if settings.trigger_prefix:
                boundary_ok = preceding is not None and preceding == settings.trigger_prefix
            else:
                boundary_ok = preceding is None or not (preceding.isalpha() or preceding.isnumeric())
            if not boundary_ok:
                continue

            shortcut = self._shortcuts_by_lower_trigger.get(trigger.lower())
            if shortcut is None:
                continue
            return ExpansionMatch(shortcut, start, start + len(trigger), candidate)
        return None


def adapt_case(typed_trigger: str, expansion: str) -> str:
    """aText-style auto-capitalisation (see CaseAdapter.swift)."""
    letters = "".join(c for c in typed_trigger if c.isalpha())
    if not letters:
        return expansion
    if letters == letters.upper() and letters != letters.lower():
        return expansion.upper()
    if letters[0].isupper():
        if not expansion:
            return expansion
        return expansion[0].upper() + expansion[1:]
    return expansion


# --------------------------------------------------------------------------------------------
# Placeholders
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ProcessedExpansion:
    #: Final text to insert, with ``{cursor}`` already stripped out.
    text: str
    #: Character offset into ``text`` where the caret should land, or None for "at the end".
    cursor_offset: Optional[int]


_DATE_RE = re.compile(r"\{date(?::([^}]+))?\}")
_TIME_RE = re.compile(r"\{time(?::([^}]+))?\}")
_CURSOR = "{cursor}"
DEFAULT_DATE_PATTERN = "MMM d, yyyy"
DEFAULT_TIME_PATTERN = "h:mm a"


def process_placeholders(raw: str, now: Optional[_dt.datetime] = None) -> ProcessedExpansion:
    """Resolve ``{date}``, ``{time}`` (optionally ``{date:pattern}``) and ``{cursor}``."""
    now = now or _dt.datetime.now().astimezone()
    text = _DATE_RE.sub(lambda m: format_icu(m.group(1) or DEFAULT_DATE_PATTERN, now), raw)
    text = _TIME_RE.sub(lambda m: format_icu(m.group(1) or DEFAULT_TIME_PATTERN, now), text)

    cursor_offset: Optional[int] = None
    idx = text.find(_CURSOR)
    if idx != -1:
        cursor_offset = idx
        text = text[:idx] + text[idx + len(_CURSOR):]
    return ProcessedExpansion(text, cursor_offset)


def format_icu(pattern: str, when: _dt.datetime) -> str:
    """Format ``when`` with an ICU/Java-style pattern (``yyyy-MM-dd``, ``h:mm a`` ...).

    The patterns come from the shared shortcut format, which was designed around
    ``DateFormatter`` / ``SimpleDateFormat`` — hence not ``strftime``. Month and day names follow
    the process locale.
    """
    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "'":
            if i + 1 < n and pattern[i + 1] == "'":
                out.append("'")
                i += 2
                continue
            j, lit = i + 1, []
            while j < n:
                if pattern[j] == "'":
                    if j + 1 < n and pattern[j + 1] == "'":
                        lit.append("'")
                        j += 2
                        continue
                    break
                lit.append(pattern[j])
                j += 1
            out.append("".join(lit))
            i = j + 1
            continue
        if c.isascii() and c.isalpha():
            j = i
            while j < n and pattern[j] == c:
                j += 1
            out.append(_icu_field(c, j - i, when))
            i = j
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _icu_field(letter: str, count: int, d: _dt.datetime) -> str:
    def pad(value: int) -> str:
        return f"{value:0{count}d}"

    if letter == "y":
        return f"{d.year % 100:02d}" if count == 2 else pad(d.year)
    if letter in "ML":
        if count <= 2:
            return pad(d.month)
        if count == 3:
            return d.strftime("%b")
        full = d.strftime("%B")
        return full if count == 4 else full[:1]
    if letter == "d":
        return pad(d.day)
    if letter == "D":
        return pad(d.timetuple().tm_yday)
    if letter == "E":
        if count <= 3:
            return d.strftime("%a")
        full = d.strftime("%A")
        return full if count == 4 else full[:1]
    if letter == "a":
        # Some locales (e.g. fr_FR) have an empty AM/PM string.
        return d.strftime("%p") or ("AM" if d.hour < 12 else "PM")
    if letter == "H":
        return pad(d.hour)
    if letter == "h":
        return pad(d.hour % 12 or 12)
    if letter == "K":
        return pad(d.hour % 12)
    if letter == "k":
        return pad(d.hour or 24)
    if letter == "m":
        return pad(d.minute)
    if letter == "s":
        return pad(d.second)
    if letter == "S":
        return f"{d.microsecond:06d}"[:count].ljust(count, "0")
    if letter == "z":
        return d.tzname() or ""
    if letter in "ZXx":
        return d.strftime("%z")
    return letter * count
