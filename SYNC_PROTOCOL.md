# KeyFlow sync protocol (`keyflow-sync.json`)

Both apps read and write the same file in a folder mirrored by Syncthing (or anything that copies
a folder byte-for-byte). No server, no direct device-to-device traffic — just this one file.

**The canonical format is the one the Android app writes** (`SyncFileFormat.kt`). The macOS app
matches it exactly. An earlier macOS draft used a different shape (`id`/`folderId`, ISO-8601
dates); the two apps then couldn't parse each other's file and each overwrote the other's data.
If either side changes the format, change this document first and update both.

## File format

```json
{
  "version": 1,
  "folders": [
    { "syncId": "0123456789abcdef0123456789abcdef", "name": "Work", "colorTag": null,
      "sortOrder": 0, "dateCreated": 1700000000000, "dateModified": 1700000005000, "deletedAt": null }
  ],
  "shortcuts": [
    { "syncId": "5a4543dc-1709-4a4a-9cbc-ea7d7bc02ba2", "trigger": ";addr", "expansion": "123 Main St",
      "isRichText": false, "group": null, "enabled": true,
      "dateCreated": 1700000000000, "dateModified": 1700000123456, "deletedAt": null,
      "folderSyncId": "0123456789abcdef0123456789abcdef" }
  ]
}
```

- **Timestamps are integer epoch milliseconds** (never floats or strings).
- Absent nullable fields are written as explicit `null`. Readers are lenient: only `syncId` is
  required; anything else missing falls back to a default (`enabled` → true, numbers → 0, …), and
  an entry that can't be read is skipped rather than failing the whole file.
- `folderSyncId` references a folder's `syncId`, never a device-local id. A shortcut pointing at a
  folder the reader doesn't have goes to "Uncategorized".
- The file is always the writer's **complete state**: every live record plus every tombstone it
  still holds. Never a diff.

## Identity: `syncId` is an opaque string

Local ids differ per device, so identity travels as `syncId`. Treat it as an **opaque string and
write back exactly what you received.** Android produces two shapes — lowercase dashed UUIDs
(`UUID.randomUUID().toString()`) for newer rows and 32-char lowercase hex
(`lower(hex(randomblob(16)))`) for rows migrated from before sync existed. A device that
re-spells an id (adds dashes, changes case) makes the other side see a *new* record and duplicate
it. New records use the lowercase dashed UUID form.

### First contact: adopting identity instead of duplicating

Two devices that each built the same shortcuts independently (say one imported the other's export)
hold them under unrelated ids. Merging by id alone would insert every record a second time, on
both sides. So when a live record arrives whose `syncId` this device has never seen, and a local
live record **the file doesn't know about** has the same trigger (folders: same name,
case-insensitive, trimmed), they are the same record: the local one adopts the incoming `syncId`
instead of a duplicate being created. Each local record is claimed at most once.

## Merge (applied per record type)

For each incoming record, compare against this device's copy of the same `syncId`:

1. `incoming = max(dateModified, deletedAt ?? dateModified)`; `local` = the live record's
   `dateModified`, or a held tombstone's timestamp, or *none* if never seen.
2. If `local` exists and `incoming <= local` → **ignore**. Incoming must be *strictly* newer, so
   ties go to local and a device re-reading its own write changes nothing.
3. Otherwise a record with `deletedAt` set → **delete** locally and keep the tombstone verbatim;
   a live record → **upsert**, keeping its `dateModified` exactly as given (never re-stamped with
   "now" — that would make every merge look like a fresh local edit).

Absence from the file is **not** a delete signal; only an explicit tombstone deletes.

## Deletes are soft

A delete sets `deletedAt` **and** `dateModified` to the same "now" (Android does exactly this).
The `dateModified` bump matters: the receiving side accepts a change only if it is newer than its
own copy, so a tombstone that kept its old `dateModified` would be ignored. Tombstones keep the
record's last field values. A device may prune tombstones after ~30 days.

## Write rule: never write without reading first

Every path that writes the file — a debounced local edit, "Sync now", startup, turning sync on —
must **pull first, then push**: merge whatever is on disk into local state, *then* serialize. A
plain "write my state" overwrites any change that arrived but hasn't been merged yet; that is the
bug this rule exists to prevent.

- Writes are debounced ~2 s after the last local change, so a burst of edits is one write.
- Before writing, compare against the file on disk (ignoring record order and `version`). If it
  already says the same thing, **don't write** — it avoids churn and two devices rewriting each
  other's file just because their JSON ordering differs.
- Don't overwrite a file that fails to parse if it was modified in the last ~10 s: another device
  is probably mid-write. Wait and retry.
- **Writes should be atomic where the platform allows it** (macOS: write a temp file, rename it
  into place). **Android's Storage Access Framework doesn't allow it:** renaming onto an existing
  file is refused, and delete-then-rename leaves the file *absent* for a moment, which Syncthing
  propagates to every other device as a deletion — worse than a partial read. So Android writes in
  place, once, and only when the content actually differs (above). Because any writer may be
  caught half-written, **readers must treat an unparseable file as "not ready yet" and re-read it
  on the next poll** — not as an empty file, and not as "already seen" (record the file's mtime
  only after it parsed).
- Records a device can't key (unrecognized `syncId`) are carried through untouched, not dropped.

## Detecting changes

- On startup, pull-then-push once **before** arming any watcher (changes may have arrived while
  the app was closed).
- Poll the file's modification time (~5–10 s while running) and re-read when it moves. This is the
  reliable mechanism: a folder watcher only sees files added/renamed into place (what Syncthing
  does), and **misses a file rewritten in place** (what Android's writer does).
- A folder watcher is a fine extra for immediacy on top of polling, never a replacement.
- Ignore events caused by your own write (compare against the bytes you just wrote).
