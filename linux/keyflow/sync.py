"""Folder sync through a single JSON file (``keyflow-sync.json``), as used with Syncthing.

Port of ``SyncModels`` / ``SyncMerger`` / ``SyncIdentityAdopter`` / ``SyncEngine`` from the macOS
app. The wire format and merge rules are specified in SYNC_PROTOCOL.md at the repository root and
are shared with the Android app; read that first. In short: the file is always a device's complete
state, records are identified by an opaque ``syncId``, the strictly newer timestamp wins, deletes
are tombstones, and every write is *pull first, then push*.

What differs from macOS: the local store has no ids or timestamps of its own. Each shortcut in
``shortcuts.json`` carries a ``syncId``, and ``sync-state.json`` remembers what each syncId looked
like at the last sync (plus tombstones). Comparing the current shortcuts with that snapshot
detects edits and deletions, including ones made by hand in an editor.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Optional

from .core import Shortcut
from .store import Store, _atomic_write

log = logging.getLogger("keyflow")

SYNC_FILE_NAME = "keyflow-sync.json"
FORMAT_VERSION = 1
TOMBSTONE_MAX_AGE_MS = 30 * 24 * 3600 * 1000
#: An unreadable file modified this recently is probably another device mid-write.
MID_WRITE_WINDOW_SECONDS = 10.0

DEBOUNCE_SECONDS = 2.0
POLL_SECONDS = 5.0
RETRY_SECONDS = 10.0
ERROR_RETRY_SECONDS = 30.0


def now_ms() -> int:
    return int(round(time.time() * 1000))


def new_sync_id() -> str:
    """New records use the lowercase dashed UUID form (see SYNC_PROTOCOL.md)."""
    return str(uuid.uuid4())


# --------------------------------------------------------------------------------------------
# Wire model
# --------------------------------------------------------------------------------------------


def _blank_to_none(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value.strip(" \t") else None


def _str(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _bool(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _int(value: Any, default: Optional[int]) -> Optional[int]:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value == int(value):
        return int(value)
    return default


def _norm(key: Optional[str]) -> str:
    return (key or "").strip().lower()


@dataclass
class ShortcutEntry:
    """A shortcut as written to the sync file. Field names match the Android ``SyncFileFormat``."""

    sync_id: str
    trigger: str = ""
    expansion: str = ""
    is_rich_text: bool = False
    group: Optional[str] = None
    enabled: bool = True
    date_created: int = 0
    date_modified: int = 0
    deleted_at: Optional[int] = None
    folder_sync_id: Optional[str] = None

    @property
    def effective_timestamp(self) -> int:
        # A tombstone's dateModified normally equals deletedAt; the max keeps a stray older
        # dateModified from hiding a delete.
        return max(self.date_modified, self.deleted_at if self.deleted_at is not None else self.date_modified)

    def to_json(self) -> dict[str, Any]:
        return {
            "syncId": self.sync_id, "trigger": self.trigger, "expansion": self.expansion,
            "isRichText": self.is_rich_text, "group": self.group, "enabled": self.enabled,
            "dateCreated": self.date_created, "dateModified": self.date_modified,
            "deletedAt": self.deleted_at, "folderSyncId": self.folder_sync_id,
        }

    @classmethod
    def from_json(cls, d: Any) -> Optional["ShortcutEntry"]:
        """Lenient like Android's ``opt*`` parsing: only a non-blank ``syncId`` is mandatory."""
        if not isinstance(d, dict) or not _blank_to_none(d.get("syncId")):
            return None
        return cls(
            sync_id=d["syncId"], trigger=_str(d.get("trigger")), expansion=_str(d.get("expansion")),
            is_rich_text=_bool(d.get("isRichText"), False), group=_blank_to_none(d.get("group")),
            enabled=_bool(d.get("enabled"), True), date_created=_int(d.get("dateCreated"), 0),
            date_modified=_int(d.get("dateModified"), 0), deleted_at=_int(d.get("deletedAt"), None),
            folder_sync_id=_blank_to_none(d.get("folderSyncId")),
        )


@dataclass
class FolderEntry:
    sync_id: str
    name: str = ""
    color_tag: Optional[str] = None
    sort_order: int = 0
    date_created: int = 0
    date_modified: int = 0
    deleted_at: Optional[int] = None

    @property
    def effective_timestamp(self) -> int:
        return max(self.date_modified, self.deleted_at if self.deleted_at is not None else self.date_modified)

    def to_json(self) -> dict[str, Any]:
        return {
            "syncId": self.sync_id, "name": self.name, "colorTag": self.color_tag,
            "sortOrder": self.sort_order, "dateCreated": self.date_created,
            "dateModified": self.date_modified, "deletedAt": self.deleted_at,
        }

    @classmethod
    def from_json(cls, d: Any) -> Optional["FolderEntry"]:
        if not isinstance(d, dict) or not _blank_to_none(d.get("syncId")):
            return None
        return cls(
            sync_id=d["syncId"], name=_str(d.get("name")), color_tag=_blank_to_none(d.get("colorTag")),
            sort_order=_int(d.get("sortOrder"), 0), date_created=_int(d.get("dateCreated"), 0),
            date_modified=_int(d.get("dateModified"), 0), deleted_at=_int(d.get("deletedAt"), None),
        )


@dataclass
class SyncFile:
    """The full contents of ``keyflow-sync.json``: always a complete snapshot, never a diff."""

    folders: list[FolderEntry] = field(default_factory=list)
    shortcuts: list[ShortcutEntry] = field(default_factory=list)
    version: int = FORMAT_VERSION

    @classmethod
    def decode(cls, data: bytes) -> "SyncFile":
        """Raises ValueError if the file is not (yet) a JSON object, e.g. while still syncing."""
        try:
            raw = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as e:
            raise ValueError(f"not valid JSON: {e}") from e
        if not isinstance(raw, dict):
            raise ValueError("not a JSON object")
        folders_raw = raw.get("folders") if isinstance(raw.get("folders"), list) else []
        shortcuts_raw = raw.get("shortcuts") if isinstance(raw.get("shortcuts"), list) else []
        # An entry that can't be read is skipped, not fatal for the whole file.
        return cls(
            folders=[e for e in map(FolderEntry.from_json, folders_raw) if e],
            shortcuts=[e for e in map(ShortcutEntry.from_json, shortcuts_raw) if e],
            version=_int(raw.get("version"), FORMAT_VERSION),
        )

    def encode(self) -> bytes:
        doc = {
            "version": self.version,
            "folders": [f.to_json() for f in self.folders],
            "shortcuts": [s.to_json() for s in self.shortcuts],
        }
        return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8")

    def is_equivalent(self, other: "SyncFile") -> bool:
        """Same records, ignoring order and ``version``: lets a push skip a no-op write."""
        def norm(items: list) -> list:
            return sorted(items, key=lambda e: e.sync_id)

        return norm(self.folders) == norm(other.folders) and norm(self.shortcuts) == norm(other.shortcuts)


# --------------------------------------------------------------------------------------------
# Merge decisions (SyncMerger) and first-contact identity adoption (SyncIdentityAdopter)
# --------------------------------------------------------------------------------------------

UPSERT, DELETE, IGNORE = "upsert", "delete", "ignore"


def resolve(incoming: "ShortcutEntry | FolderEntry", local_timestamp: Optional[int]) -> str:
    """What to do with one incoming record.

    ``local_timestamp`` is the live local record's ``dateModified``, a held tombstone's timestamp,
    or None if this device has never seen the id. Incoming must be *strictly* newer to win, so
    ties go to local and re-reading our own write changes nothing.
    """
    if local_timestamp is not None and incoming.effective_timestamp <= local_timestamp:
        return IGNORE
    return DELETE if incoming.deleted_at is not None else UPSERT


def pair_identities(
    incoming: list[tuple[str, str]], locals_: list[tuple[str, str]]
) -> list[tuple[str, str]]:
    """Match never-seen incoming records to local ones the file doesn't know, by key.

    ``incoming``: (sync_id, key); ``locals_``: (local sync_id, key). Returns (incoming sync_id,
    local sync_id) pairs. Each local record is claimed at most once, first come first served;
    blank keys never match.
    """
    unclaimed: dict[str, list[str]] = {}
    for local_id, key in locals_:
        k = _norm(key)
        if k:
            unclaimed.setdefault(k, []).append(local_id)
    pairs = []
    for sync_id, key in incoming:
        k = _norm(key)
        if k and unclaimed.get(k):
            pairs.append((sync_id, unclaimed[k].pop(0)))
    return pairs


# --------------------------------------------------------------------------------------------
# Local sync state
# --------------------------------------------------------------------------------------------


@dataclass
class SyncState:
    """What this device knew at the last sync, keyed by ``syncId`` (kept in sync-state.json)."""

    initialized: bool = False
    shortcuts: dict[str, ShortcutEntry] = field(default_factory=dict)
    folders: dict[str, FolderEntry] = field(default_factory=dict)
    tomb_shortcuts: dict[str, ShortcutEntry] = field(default_factory=dict)
    tomb_folders: dict[str, FolderEntry] = field(default_factory=dict)
    last_synced_ms: Optional[int] = None
    last_error: Optional[str] = None
    #: Identity adoptions made during this run (old local id -> adopted id); not persisted.
    rekeyed: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "SyncState":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError):
            log.warning("%s is unreadable; starting from a fresh sync state", path)
            return cls()
        if not isinstance(raw, dict):
            return cls()

        def entries(key: str, parser: Callable[[Any], Any]) -> dict[str, Any]:
            items = raw.get(key) if isinstance(raw.get(key), list) else []
            return {e.sync_id: e for e in map(parser, items) if e}

        return cls(
            initialized=_bool(raw.get("initialized"), False),
            shortcuts=entries("shortcuts", ShortcutEntry.from_json),
            folders=entries("folders", FolderEntry.from_json),
            tomb_shortcuts=entries("tombstoneShortcuts", ShortcutEntry.from_json),
            tomb_folders=entries("tombstoneFolders", FolderEntry.from_json),
            last_synced_ms=_int(raw.get("lastSyncedAt"), None),
            last_error=_blank_to_none(raw.get("lastError")),
        )

    def save(self, path: Path) -> None:
        doc = {
            "initialized": self.initialized,
            "lastSyncedAt": self.last_synced_ms,
            "lastError": self.last_error,
            "shortcuts": [e.to_json() for e in self.shortcuts.values()],
            "folders": [e.to_json() for e in self.folders.values()],
            "tombstoneShortcuts": [e.to_json() for e in self.tomb_shortcuts.values()],
            "tombstoneFolders": [e.to_json() for e in self.tomb_folders.values()],
        }
        _atomic_write(path, json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n")

    def prune(self, now: int) -> None:
        """Drop tombstones older than 30 days."""
        cutoff = now - TOMBSTONE_MAX_AGE_MS
        self.tomb_shortcuts = {k: v for k, v in self.tomb_shortcuts.items() if v.effective_timestamp >= cutoff}
        self.tomb_folders = {k: v for k, v in self.tomb_folders.items() if v.effective_timestamp >= cutoff}

    # -- helpers ----------------------------------------------------------------------------

    def folder_name(self, folder_sync_id: Optional[str]) -> Optional[str]:
        """Name of a *live* folder, else None (a deleted/unknown folder means Uncategorized)."""
        f = self.folders.get(folder_sync_id) if folder_sync_id else None
        return f.name if f else None

    def local_shortcut_ts(self, sync_id: str) -> Optional[int]:
        if sync_id in self.shortcuts:
            return self.shortcuts[sync_id].date_modified
        t = self.tomb_shortcuts.get(sync_id)
        return t.effective_timestamp if t else None

    def local_folder_ts(self, sync_id: str) -> Optional[int]:
        if sync_id in self.folders:
            return self.folders[sync_id].date_modified
        t = self.tomb_folders.get(sync_id)
        return t.effective_timestamp if t else None


# --------------------------------------------------------------------------------------------
# Local changes -> state, state -> local shortcuts
# --------------------------------------------------------------------------------------------


def _content(trigger: str, expansion: str, rich: bool, group: Optional[str], enabled: bool,
             folder_name: Optional[str]) -> tuple:
    return (trigger, expansion, rich, _blank_to_none(group), enabled, _norm(folder_name))


def reconcile_local(state: SyncState, shortcuts: list[Shortcut], edit_ms: int) -> None:
    """Bring ``state`` in line with the shortcuts as they are on disk right now.

    Stamps edits, gives new shortcuts a ``syncId`` (set on the Shortcut objects), and turns
    shortcuts that disappeared into tombstones.

    ``edit_ms`` is the best estimate of *when* the user made those changes: the modification time
    of shortcuts.json, capped at the current time. It is not simply "now", because the edits may
    have been made while the daemon was off, and an edit made yesterday must not beat a newer edit
    from another device just because this device synced later. Likewise on the very first sync it
    stops stale local data from beating newer remote data.
    """
    stamp = edit_ms

    def folder_id_for(name: Optional[str]) -> Optional[str]:
        key = _norm(name)
        if not key:
            return None
        for fid, f in state.folders.items():
            if _norm(f.name) == key:
                return fid
        fid = new_sync_id()
        state.folders[fid] = FolderEntry(fid, name.strip(), None, 0, stamp, stamp, None)
        return fid

    seen: set[str] = set()
    pending: list[Shortcut] = []

    def apply(s: Shortcut, entry: ShortcutEntry) -> None:
        if _content(entry.trigger, entry.expansion, entry.is_rich_text, entry.group, entry.enabled,
                    state.folder_name(entry.folder_sync_id)) != \
                _content(s.trigger, s.expansion, s.is_rich_text, s.group, s.enabled, s.folder):
            entry.trigger, entry.expansion = s.trigger, s.expansion
            entry.is_rich_text, entry.group = s.is_rich_text, _blank_to_none(s.group)
            entry.enabled = s.enabled
            entry.folder_sync_id = folder_id_for(s.folder)
            # strictly newer than the version it replaces, even if another device's clock runs ahead
            entry.date_modified = max(stamp, entry.date_modified + 1)
        s.sync_id = entry.sync_id
        seen.add(entry.sync_id)

    for s in shortcuts:
        if s.sync_id and s.sync_id not in seen and s.sync_id in state.shortcuts:
            apply(s, state.shortcuts[s.sync_id])
        else:
            pending.append(s)

    for s in pending:
        # No usable id (hand-added, or a duplicated one): a not-yet-claimed entry with the same
        # trigger is the same shortcut; otherwise it is genuinely new.
        match = next((e for e in state.shortcuts.values()
                      if e.sync_id not in seen and _norm(e.trigger) == _norm(s.trigger)), None)
        if match is not None:
            apply(s, match)
            continue
        sid = s.sync_id if s.sync_id and s.sync_id not in seen else new_sync_id()
        state.tomb_shortcuts.pop(sid, None)  # restored after a local delete: live again
        state.shortcuts[sid] = ShortcutEntry(
            sid, s.trigger, s.expansion, s.is_rich_text, _blank_to_none(s.group), s.enabled,
            stamp, stamp, None, folder_id_for(s.folder),
        )
        s.sync_id = sid
        seen.add(sid)

    for sid in [k for k in state.shortcuts if k not in seen]:
        gone = state.shortcuts.pop(sid)
        deleted = max(stamp, gone.date_modified + 1)
        state.tomb_shortcuts[sid] = replace(gone, deleted_at=deleted, date_modified=deleted)
    state.initialized = True


def project(state: SyncState, current: list[Shortcut]) -> list[Shortcut]:
    """The shortcuts that should be on disk after a merge, keeping the current order."""
    order = {state.rekeyed.get(s.sync_id, s.sync_id): i for i, s in enumerate(current) if s.sync_id}

    def key(sid: str) -> tuple:
        e = state.shortcuts[sid]
        return (0, order[sid], "") if sid in order else (1, 0, _norm(e.trigger))

    out = []
    for sid in sorted(state.shortcuts, key=key):
        e = state.shortcuts[sid]
        out.append(Shortcut(e.trigger, e.expansion, e.is_rich_text, e.group, e.enabled,
                            state.folder_name(e.folder_sync_id), e.sync_id))
    return out


def build_file(state: SyncState) -> SyncFile:
    folders = list(state.folders.values()) + [t for k, t in state.tomb_folders.items() if k not in state.folders]
    shortcuts = list(state.shortcuts.values()) + [
        t for k, t in state.tomb_shortcuts.items() if k not in state.shortcuts
    ]
    return SyncFile(
        folders=sorted(folders, key=lambda e: e.sync_id),
        shortcuts=sorted(shortcuts, key=lambda e: e.sync_id),
    )


# --------------------------------------------------------------------------------------------
# Merging a remote file into the state
# --------------------------------------------------------------------------------------------


def merge_file(state: SyncState, file: SyncFile, now: int) -> None:
    _adopt_identities(state, file)
    cutoff = now - TOMBSTONE_MAX_AGE_MS

    def expired_unknown(entry: "ShortcutEntry | FolderEntry", local_ts: Optional[int]) -> bool:
        # Re-adopting a tombstone we already pruned would make pruning pointless.
        return entry.deleted_at is not None and local_ts is None and entry.effective_timestamp < cutoff

    # Folders first: shortcuts reference them.
    for f in file.folders:
        if expired_unknown(f, state.local_folder_ts(f.sync_id)):
            continue
        action = resolve(f, state.local_folder_ts(f.sync_id))
        if action == UPSERT:
            state.folders[f.sync_id] = replace(f)
            state.tomb_folders.pop(f.sync_id, None)
        elif action == DELETE:
            state.folders.pop(f.sync_id, None)
            state.tomb_folders[f.sync_id] = replace(f)

    for s in file.shortcuts:
        if expired_unknown(s, state.local_shortcut_ts(s.sync_id)):
            continue
        action = resolve(s, state.local_shortcut_ts(s.sync_id))
        if action == UPSERT:
            state.shortcuts[s.sync_id] = replace(s)  # dateModified kept exactly as given
            state.tomb_shortcuts.pop(s.sync_id, None)
        elif action == DELETE:
            state.shortcuts.pop(s.sync_id, None)
            state.tomb_shortcuts[s.sync_id] = replace(s)


def _adopt_identities(state: SyncState, file: SyncFile) -> None:
    """A live record the file has and we've never seen, matching a local record the file doesn't
    know, is the same record: the local one takes the file's id instead of being duplicated."""
    file_folder_ids = {f.sync_id for f in file.folders}
    unknown = [(f.sync_id, f.name) for f in file.folders
               if f.deleted_at is None and state.local_folder_ts(f.sync_id) is None]
    local = [(fid, f.name) for fid, f in state.folders.items() if fid not in file_folder_ids]
    for new_id, old_id in pair_identities(unknown, local):
        entry = state.folders.pop(old_id)
        entry.sync_id = new_id
        state.folders[new_id] = entry
        for s in state.shortcuts.values():
            if s.folder_sync_id == old_id:
                s.folder_sync_id = new_id

    file_ids = {s.sync_id for s in file.shortcuts}
    unknown = [(s.sync_id, s.trigger) for s in file.shortcuts
               if s.deleted_at is None and state.local_shortcut_ts(s.sync_id) is None]
    local = [(sid, s.trigger) for sid, s in state.shortcuts.items() if sid not in file_ids]
    for new_id, old_id in pair_identities(unknown, local):
        entry = state.shortcuts.pop(old_id)
        entry.sync_id = new_id
        state.shortcuts[new_id] = entry
        state.rekeyed[old_id] = new_id


# --------------------------------------------------------------------------------------------
# One sync pass
# --------------------------------------------------------------------------------------------


@dataclass
class SyncResult:
    ok: bool
    message: str
    #: Try again soon (e.g. the file looked half-written).
    retry: bool = False
    wrote_remote: bool = False
    changed_local: bool = False


class SyncEngine:
    def __init__(
        self,
        store: Store,
        folder: "str | Path",
        clock_ms: Callable[[], int] = now_ms,
        file_mtime_ms: Optional[Callable[[], int]] = None,
    ) -> None:
        self.store = store
        self.folder = Path(folder).expanduser()
        self.path = self.folder / SYNC_FILE_NAME
        self._clock = clock_ms
        self._file_mtime_ms = file_mtime_ms

    def remote_mtime(self) -> Optional[int]:
        try:
            return self.path.stat().st_mtime_ns
        except OSError:
            return None

    def _edit_ms(self, now: int) -> int:
        """When the local shortcuts were last edited (mtime of shortcuts.json), never in the future."""
        if self._file_mtime_ms:
            return min(now, self._file_mtime_ms())
        try:
            return min(now, int(self.store.shortcuts_path.stat().st_mtime * 1000))
        except OSError:
            return now

    def sync_once(self) -> SyncResult:
        """Pull, then push. Never writes the sync file without having merged it first."""
        try:
            with self.store.lock():
                return self._sync_locked()
        except (OSError, ValueError) as e:
            return SyncResult(False, str(e))

    def _sync_locked(self) -> SyncResult:
        store = self.store
        shortcuts = store.load_shortcuts()  # ValueError on a corrupt file: report, never overwrite
        original = [replace(s) for s in shortcuts]
        state = SyncState.load(store.sync_state_path)
        now = self._clock()

        if not self.folder.is_dir():
            return self._record(state, now, SyncResult(False, f"sync folder {self.folder} does not exist"))

        state.prune(now)
        reconcile_local(state, shortcuts, self._edit_ms(now))

        # -- pull -----------------------------------------------------------------------
        note: Optional[str] = None
        try:
            data = self.path.read_bytes()
        except FileNotFoundError:
            data = None
        if data is not None:
            try:
                merge_file(state, SyncFile.decode(data), now)
            except ValueError:
                note = f"{SYNC_FILE_NAME} couldn't be read, it may still be syncing"

        # -- push -----------------------------------------------------------------------
        file = build_file(state)
        need_write, waiting = True, False
        try:
            existing_bytes: Optional[bytes] = self.path.read_bytes()
        except FileNotFoundError:
            existing_bytes = None
        if existing_bytes is not None:
            try:
                if SyncFile.decode(existing_bytes).is_equivalent(file):
                    need_write = False  # already says exactly this: don't touch it
            except ValueError:
                # Unreadable: probably another device mid-write. Don't overwrite a fresh one.
                if time.time() - self.path.stat().st_mtime < MID_WRITE_WINDOW_SECONDS:
                    waiting = True
        wrote = need_write and not waiting
        if wrote:
            self._write_atomic(file.encode())

        # -- local projection ---------------------------------------------------------------
        projected = project(state, shortcuts)
        changed_local = projected != original
        if changed_local:
            store.save_shortcuts(projected)

        if waiting:
            result = SyncResult(True, f"{SYNC_FILE_NAME} is being written by another device, waiting",
                                retry=True, changed_local=changed_local)
        elif note:
            result = SyncResult(True, note + "; replaced with this device's state", wrote_remote=wrote,
                                changed_local=changed_local)
        else:
            result = SyncResult(True, "in sync", wrote_remote=wrote, changed_local=changed_local)
        return self._record(state, now, result)

    def _record(self, state: SyncState, now: int, result: SyncResult) -> SyncResult:
        if result.ok and not result.retry:
            state.last_synced_ms = now
        state.last_error = None if result.ok and not result.retry else result.message
        state.save(self.store.sync_state_path)
        return result

    def _write_atomic(self, data: bytes) -> None:
        # Syncthing ignores ".syncthing.*.tmp" names, so the temp file is never uploaded; the final
        # rename shows up as one complete file (a delete-then-write would sync as a deletion).
        tmp = self.folder / f".syncthing.{SYNC_FILE_NAME}.{os.getpid()}.tmp"
        try:
            with open(tmp, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


# --------------------------------------------------------------------------------------------
# Scheduling (used by the daemon)
# --------------------------------------------------------------------------------------------


class SyncRunner:
    """Decides *when* to sync: at startup, ~2 s after a local edit, and when the file changes.

    Polling the file's mtime is the reliable trigger, because another device may rewrite the file
    in place; a directory watcher would miss that (see SYNC_PROTOCOL.md, "Detecting changes").
    """

    def __init__(self, store: Store, clock: Callable[[], float] = time.monotonic) -> None:
        self.store = store
        self._clock = clock
        self._config: tuple = (False, "")
        self._engine: Optional[SyncEngine] = None
        self._due: Optional[float] = None
        self._next_poll = 0.0
        self._remote_mtime: Optional[int] = None
        self._last_fp: Optional[tuple] = None
        self._last_logged: Optional[str] = None

    def configure(self, enabled: bool, folder: str) -> None:
        if (enabled, folder) == self._config:
            return
        self._config = (enabled, folder)
        if enabled and folder:
            self._engine = SyncEngine(self.store, folder)
            self._due = self._clock()  # startup: pull-then-push before anything else
            self._next_poll = 0.0
            self._remote_mtime = None
            self._last_fp = self.store.shortcuts_fingerprint()
            log.info("sync enabled: %s", folder)
        else:
            self._engine = None
            self._due = None
            log.info("sync disabled")

    def _schedule(self, at: float) -> None:
        self._due = at if self._due is None else min(self._due, at)

    def tick(self) -> None:
        if self._engine is None:
            return
        now = self._clock()

        fp = self.store.shortcuts_fingerprint()
        if fp != self._last_fp:
            self._last_fp = fp
            self._schedule(now + DEBOUNCE_SECONDS)  # debounce a burst of edits into one pass

        if now >= self._next_poll:
            self._next_poll = now + POLL_SECONDS
            if self._engine.remote_mtime() != self._remote_mtime:
                self._schedule(now)

        if self._due is not None and now >= self._due:
            self._run(now)

    def _run(self, now: float) -> None:
        assert self._engine is not None
        result = self._engine.sync_once()
        # Remember our own writes so they are not mistaken for outside changes.
        self._last_fp = self.store.shortcuts_fingerprint()
        self._remote_mtime = self._engine.remote_mtime()
        if not result.ok:
            self._due = now + ERROR_RETRY_SECONDS
        elif result.retry:
            self._due = now + RETRY_SECONDS
        else:
            self._due = None
        if result.message != self._last_logged or result.changed_local or result.wrote_remote:
            level = logging.INFO if result.ok else logging.WARNING
            log.log(level, "sync: %s%s%s", result.message,
                    " (updated local shortcuts)" if result.changed_local else "",
                    " (updated sync file)" if result.wrote_remote else "")
        self._last_logged = result.message
