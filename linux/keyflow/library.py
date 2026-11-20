"""Edit operations on the shortcut library (shortcuts + folders), shared by the GUI and CLI.

Every operation is one locked read-modify-write of ``shortcuts.json``; the running daemon notices
the new file and reloads, and (if sync is on) pushes the change to other devices. Operations
return an error message (str) when they refuse, otherwise None / a result, so UI code never has
to catch exceptions for ordinary validation problems.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Iterable, Optional

from . import importers
from .core import FOLDER_COLOR_PALETTE, AppSettings, Folder, Shortcut
from .store import Library, LinuxConfig, Store


ALL, UNCATEGORIZED = "all", "none"   # folder-filter sentinels; anything else is a folder name


def filter_shortcuts(shortcuts: list[Shortcut], query: str, group: Optional[str],
                     folder_filter: str, scoped: bool) -> list[Shortcut]:
    """The list the Shortcuts tab shows (port of ShortcutListView.filtered).

    Like Android, an unscoped search ignores the folder filter and searches every folder.
    """
    by_group = [s for s in shortcuts if group is None or s.group == group]

    def in_folder(s: Shortcut) -> bool:
        if folder_filter == ALL:
            return True
        if folder_filter == UNCATEGORIZED:
            return not s.folder
        return (s.folder or "").lower() == folder_filter.lower()

    def matches(s: Shortcut) -> bool:
        q = query.lower()
        return not q or q in s.trigger.lower() or q in s.expansion.lower()

    if not query:
        return [s for s in by_group if in_folder(s)]
    if scoped:
        return [s for s in by_group if in_folder(s) and matches(s)]
    return [s for s in by_group if matches(s)]


def _same(a: Shortcut, b: Shortcut) -> bool:
    """Identity of a shortcut: its sync id once it has one, else its trigger."""
    if a.sync_id and b.sync_id:
        return a.sync_id == b.sync_id
    return a.trigger.lower() == b.trigger.lower()


def _find(lib: Library, ref: Shortcut) -> Optional[Shortcut]:
    return next((s for s in lib.shortcuts if _same(s, ref)), None)


def _norm(name: Optional[str]) -> str:
    return (name or "").strip().lower()


@dataclass
class ImportOutcome:
    imported: int
    skipped: list[importers.SkippedRow]


class LibraryService:
    def __init__(self, store: Store) -> None:
        self.store = store

    def load(self) -> Library:
        return self.store.load_library()

    def _edit(self, fn: Callable[[Library], Optional[str]]) -> Optional[str]:
        """Run ``fn`` on a freshly loaded library under the lock; save unless it returned an error."""
        try:
            with self.store.lock():
                lib = self.store.load_library()
                error = fn(lib)
                if error is None:
                    self.store.save_library(lib)
                return error
        except (OSError, ValueError) as e:
            return str(e)

    def update_settings(self, fn: Callable[[AppSettings, LinuxConfig], None]) -> Optional[str]:
        """Locked read-modify-write of settings.json; the daemon applies it within ~0.5 s."""
        try:
            with self.store.lock():
                settings, linux = self.store.load_settings()
                fn(settings, linux)
                self.store.save_settings(settings, linux)
        except (OSError, ValueError) as e:
            return str(e)
        return None

    # -- shortcuts ----------------------------------------------------------------------------

    def save_shortcut(self, original: Optional[Shortcut], edited: Shortcut) -> Optional[str]:
        """Add (``original`` is None) or update a shortcut. Mirrors the macOS form's validation."""
        trigger = edited.trigger.strip(" \t")
        if not trigger:
            return "Trigger can't be empty"
        if not edited.expansion:
            return "Expansion can't be empty"

        def apply(lib: Library) -> Optional[str]:
            current = _find(lib, original) if original else None
            clash = next((s for s in lib.shortcuts
                          if s.trigger.lower() == trigger.lower() and s is not current), None)
            if clash:
                return "This trigger is already used by another shortcut"
            folder = (edited.folder or "").strip() or None
            new = replace(edited, trigger=trigger, group=(edited.group or "").strip() or None,
                          folder=folder, sync_id=current.sync_id if current else None)
            if current is None:
                lib.shortcuts.append(new)
            else:
                lib.shortcuts[lib.shortcuts.index(current)] = new
            return None

        return self._edit(apply)

    def set_enabled(self, ref: Shortcut, enabled: bool) -> Optional[str]:
        def apply(lib: Library) -> Optional[str]:
            current = _find(lib, ref)
            if current is None:
                return "That shortcut no longer exists"
            current.enabled = enabled
            return None

        return self._edit(apply)

    def delete_shortcuts(self, refs: Iterable[Shortcut]) -> Optional[str]:
        refs = list(refs)
        return self._edit(lambda lib: lib.shortcuts.__setitem__(
            slice(None), [s for s in lib.shortcuts if not any(_same(s, r) for r in refs)]))

    def move_to_folder(self, refs: Iterable[Shortcut], folder: Optional[str]) -> Optional[str]:
        refs = list(refs)

        def apply(lib: Library) -> None:
            for s in lib.shortcuts:
                if any(_same(s, r) for r in refs):
                    s.folder = (folder or "").strip() or None

        return self._edit(apply)

    # -- folders ------------------------------------------------------------------------------

    def create_folder(self, name: str, color: Optional[str] = None) -> Optional[str]:
        name = name.strip()
        if not name:
            return "Folder name can't be empty"

        def apply(lib: Library) -> Optional[str]:
            if any(_norm(f.name) == _norm(name) for f in lib.folders):
                return f'A folder named "{name}" already exists'
            lib.folders.append(Folder(name, color or FOLDER_COLOR_PALETTE[len(lib.folders) % len(FOLDER_COLOR_PALETTE)],
                                      max((f.sort_order for f in lib.folders), default=-1) + 1))
            return None

        return self._edit(apply)

    def rename_folder(self, old: str, new: str) -> Optional[str]:
        new = new.strip()
        if not new:
            return "Folder name can't be empty"

        def apply(lib: Library) -> Optional[str]:
            folder = next((f for f in lib.folders if _norm(f.name) == _norm(old)), None)
            if folder is None:
                return "That folder no longer exists"
            if _norm(new) != _norm(old) and any(_norm(f.name) == _norm(new) for f in lib.folders):
                return f'A folder named "{new}" already exists'
            for s in lib.shortcuts:
                if _norm(s.folder) == _norm(old):
                    s.folder = new
            folder.name = new
            return None

        return self._edit(apply)

    def set_folder_color(self, name: str, color: Optional[str]) -> Optional[str]:
        def apply(lib: Library) -> Optional[str]:
            folder = next((f for f in lib.folders if _norm(f.name) == _norm(name)), None)
            if folder is None:
                return "That folder no longer exists"
            folder.color_tag = color
            return None

        return self._edit(apply)

    def delete_folder(self, name: str, delete_shortcuts: bool) -> Optional[str]:
        """Remove a folder; its shortcuts go to Uncategorized, or are deleted with it."""
        def apply(lib: Library) -> None:
            lib.folders = [f for f in lib.folders if _norm(f.name) != _norm(name)]
            if delete_shortcuts:
                lib.shortcuts = [s for s in lib.shortcuts if _norm(s.folder) != _norm(name)]
            else:
                for s in lib.shortcuts:
                    if _norm(s.folder) == _norm(name):
                        s.folder = None

        return self._edit(apply)

    # -- import / export ----------------------------------------------------------------------

    def import_text(self, text: str, overwrite_duplicates: bool, fmt: Optional[str] = None) -> ImportOutcome:
        """Import JSON or CSV. Without ``fmt`` the content decides (``{`` / ``[`` means JSON), like
        the macOS Settings importer."""
        if fmt is None:
            fmt = "json" if text.strip().startswith(("{", "[")) else "csv"
        parsed = importers.parse_json(text) if fmt == "json" else importers.parse_csv(text)
        outcome = ImportOutcome(0, parsed.skipped)

        def apply(lib: Library) -> None:
            resolved = importers.resolve_import(
                parsed,
                lambda t: next((s for s in lib.shortcuts if s.trigger.lower() == t.lower()), None),
                overwrite_duplicates,
            )
            for s in resolved.to_save:
                existing = next((x for x in lib.shortcuts if x.trigger.lower() == s.trigger.lower()), None)
                if existing:
                    lib.shortcuts.remove(existing)
                # An overwritten shortcut keeps its sync identity; imported ids are never trusted.
                lib.shortcuts.append(replace(s, sync_id=existing.sync_id if existing else None))
            outcome.imported = len(resolved.to_save)
            outcome.skipped = resolved.skipped

        error = self._edit(apply)
        if error:
            outcome.skipped = [importers.SkippedRow(0, "", "", "", "", "", "", error)]
        return outcome

    def export_text(self, fmt: str) -> str:
        shortcuts = self.load().shortcuts
        return importers.to_csv(shortcuts) if fmt == "csv" else importers.to_json(shortcuts)
