import Foundation

/// Epoch-millisecond conversion used by the sync wire format. Every timestamp that crosses the
/// wire — and every local-vs-incoming comparison — goes through this one rounding so the two
/// sides can never disagree by a sub-millisecond fraction.
public enum SyncTime {
    public static func ms(_ date: Date) -> Int64 {
        Int64((date.timeIntervalSince1970 * 1000).rounded())
    }

    public static func date(ms: Int64) -> Date {
        Date(timeIntervalSince1970: Double(ms) / 1000)
    }
}

/// Shared shape of a sync-file record, so `SyncMerger` is written once for shortcuts and folders.
public protocol SyncRecord {
    var syncId: String { get }
    var dateModified: Int64 { get }
    var deletedAt: Int64? { get }
}

public extension SyncRecord {
    /// A tombstone's `dateModified` equals its `deletedAt` by convention (Android stamps both with
    /// the same "now"), but taking the max keeps a stray older `dateModified` from hiding a delete.
    var effectiveTimestamp: Int64 { max(dateModified, deletedAt ?? dateModified) }
}

private func blankToNil(_ value: String?) -> String? {
    guard let value, !value.trimmingCharacters(in: .whitespaces).isEmpty else { return nil }
    return value
}

/// A shortcut as written to `keyflow-sync.json`. Field names, types and null handling match the
/// Android app's `SyncFileFormat` exactly — see SYNC_PROTOCOL.md. `folderSyncId` references a
/// `SyncFolderEntry.syncId`, never a local id.
public struct SyncShortcutEntry: Codable, Equatable, SyncRecord {
    public var syncId: String
    public var trigger: String
    public var expansion: String
    public var isRichText: Bool
    public var group: String?
    public var enabled: Bool
    public var dateCreated: Int64
    public var dateModified: Int64
    public var deletedAt: Int64?
    public var folderSyncId: String?

    public init(
        syncId: String, trigger: String, expansion: String, isRichText: Bool, group: String?, enabled: Bool,
        dateCreated: Int64, dateModified: Int64, deletedAt: Int64?, folderSyncId: String?
    ) {
        self.syncId = syncId
        self.trigger = trigger
        self.expansion = expansion
        self.isRichText = isRichText
        self.group = group
        self.enabled = enabled
        self.dateCreated = dateCreated
        self.dateModified = dateModified
        self.deletedAt = deletedAt
        self.folderSyncId = folderSyncId
    }

    /// Builds the wire entry for a local shortcut. For a tombstone pass `deletedAt`: like Android,
    /// `dateModified` is then bumped to the same instant, since the receiving side decides
    /// whether to accept a delete by comparing `dateModified`.
    public init(_ shortcut: Shortcut, syncId: String, folderSyncId: String?, deletedAt: Int64? = nil) {
        self.init(
            syncId: syncId, trigger: shortcut.trigger, expansion: shortcut.expansion,
            isRichText: shortcut.isRichText, group: shortcut.group, enabled: shortcut.enabled,
            dateCreated: SyncTime.ms(shortcut.dateCreated),
            dateModified: deletedAt ?? SyncTime.ms(shortcut.dateModified),
            deletedAt: deletedAt, folderSyncId: folderSyncId
        )
    }

    public func asShortcut(id: UUID, folderId: UUID?) -> Shortcut {
        Shortcut(
            id: id, trigger: trigger, expansion: expansion, isRichText: isRichText, group: group,
            enabled: enabled, dateCreated: SyncTime.date(ms: dateCreated),
            dateModified: SyncTime.date(ms: dateModified), folderId: folderId
        )
    }

    private enum CodingKeys: String, CodingKey {
        case syncId, trigger, expansion, isRichText, group, enabled, dateCreated, dateModified, deletedAt, folderSyncId
    }

    /// Lenient like Android's `opt*` parsing: only `syncId` is mandatory, everything else falls
    /// back to a default rather than rejecting the whole entry.
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let id = try c.decode(String.self, forKey: .syncId)
        guard !id.trimmingCharacters(in: .whitespaces).isEmpty else {
            throw DecodingError.dataCorruptedError(forKey: .syncId, in: c, debugDescription: "blank syncId")
        }
        syncId = id
        trigger = (try? c.decodeIfPresent(String.self, forKey: .trigger)) ?? ""
        expansion = (try? c.decodeIfPresent(String.self, forKey: .expansion)) ?? ""
        isRichText = (try? c.decodeIfPresent(Bool.self, forKey: .isRichText)) ?? false
        group = blankToNil((try? c.decodeIfPresent(String.self, forKey: .group)) ?? nil)
        enabled = (try? c.decodeIfPresent(Bool.self, forKey: .enabled)) ?? true
        dateCreated = (try? c.decodeIfPresent(Int64.self, forKey: .dateCreated)) ?? 0
        dateModified = (try? c.decodeIfPresent(Int64.self, forKey: .dateModified)) ?? 0
        deletedAt = (try? c.decodeIfPresent(Int64.self, forKey: .deletedAt)) ?? nil
        folderSyncId = blankToNil((try? c.decodeIfPresent(String.self, forKey: .folderSyncId)) ?? nil)
    }

    /// Writes explicit `null`s (not omitted keys), matching what Android's writer produces.
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(syncId, forKey: .syncId)
        try c.encode(trigger, forKey: .trigger)
        try c.encode(expansion, forKey: .expansion)
        try c.encode(isRichText, forKey: .isRichText)
        try c.encode(group, forKey: .group)
        try c.encode(enabled, forKey: .enabled)
        try c.encode(dateCreated, forKey: .dateCreated)
        try c.encode(dateModified, forKey: .dateModified)
        try c.encode(deletedAt, forKey: .deletedAt)
        try c.encode(folderSyncId, forKey: .folderSyncId)
    }
}

/// A folder as written to `keyflow-sync.json`. See `SyncShortcutEntry`.
public struct SyncFolderEntry: Codable, Equatable, SyncRecord {
    public var syncId: String
    public var name: String
    public var colorTag: String?
    public var sortOrder: Int
    public var dateCreated: Int64
    public var dateModified: Int64
    public var deletedAt: Int64?

    public init(
        syncId: String, name: String, colorTag: String?, sortOrder: Int,
        dateCreated: Int64, dateModified: Int64, deletedAt: Int64?
    ) {
        self.syncId = syncId
        self.name = name
        self.colorTag = colorTag
        self.sortOrder = sortOrder
        self.dateCreated = dateCreated
        self.dateModified = dateModified
        self.deletedAt = deletedAt
    }

    public init(_ folder: Folder, syncId: String, deletedAt: Int64? = nil) {
        self.init(
            syncId: syncId, name: folder.name, colorTag: folder.colorTag, sortOrder: folder.sortOrder,
            dateCreated: SyncTime.ms(folder.dateCreated),
            dateModified: deletedAt ?? SyncTime.ms(folder.dateModified), deletedAt: deletedAt
        )
    }

    public func asFolder(id: UUID) -> Folder {
        Folder(
            id: id, name: name, colorTag: colorTag, sortOrder: sortOrder,
            dateCreated: SyncTime.date(ms: dateCreated), dateModified: SyncTime.date(ms: dateModified)
        )
    }

    private enum CodingKeys: String, CodingKey {
        case syncId, name, colorTag, sortOrder, dateCreated, dateModified, deletedAt
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let id = try c.decode(String.self, forKey: .syncId)
        guard !id.trimmingCharacters(in: .whitespaces).isEmpty else {
            throw DecodingError.dataCorruptedError(forKey: .syncId, in: c, debugDescription: "blank syncId")
        }
        syncId = id
        name = (try? c.decodeIfPresent(String.self, forKey: .name)) ?? ""
        colorTag = blankToNil((try? c.decodeIfPresent(String.self, forKey: .colorTag)) ?? nil)
        sortOrder = (try? c.decodeIfPresent(Int.self, forKey: .sortOrder)) ?? 0
        dateCreated = (try? c.decodeIfPresent(Int64.self, forKey: .dateCreated)) ?? 0
        dateModified = (try? c.decodeIfPresent(Int64.self, forKey: .dateModified)) ?? 0
        deletedAt = (try? c.decodeIfPresent(Int64.self, forKey: .deletedAt)) ?? nil
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(syncId, forKey: .syncId)
        try c.encode(name, forKey: .name)
        try c.encode(colorTag, forKey: .colorTag)
        try c.encode(sortOrder, forKey: .sortOrder)
        try c.encode(dateCreated, forKey: .dateCreated)
        try c.encode(dateModified, forKey: .dateModified)
        try c.encode(deletedAt, forKey: .deletedAt)
    }
}

/// Decodes one array element without letting a single bad entry fail the whole file — Android's
/// parser skips entries it can't read (`continue`), and the Mac side does the same.
private struct Lossy<T: Decodable>: Decodable {
    let value: T?
    init(from decoder: Decoder) throws { value = try? T(from: decoder) }
}

/// The full contents of `keyflow-sync.json` — always a complete snapshot, never a diff.
public struct SyncFile: Codable {
    public static let currentVersion = 1

    public var version: Int
    public var folders: [SyncFolderEntry]
    public var shortcuts: [SyncShortcutEntry]

    public init(version: Int = SyncFile.currentVersion, folders: [SyncFolderEntry], shortcuts: [SyncShortcutEntry]) {
        self.version = version
        self.folders = folders
        self.shortcuts = shortcuts
    }

    private enum CodingKeys: String, CodingKey { case version, folders, shortcuts }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        version = (try? c.decodeIfPresent(Int.self, forKey: .version)) ?? SyncFile.currentVersion
        folders = ((try? c.decodeIfPresent([Lossy<SyncFolderEntry>].self, forKey: .folders)) ?? nil)?
            .compactMap(\.value) ?? []
        shortcuts = ((try? c.decodeIfPresent([Lossy<SyncShortcutEntry>].self, forKey: .shortcuts)) ?? nil)?
            .compactMap(\.value) ?? []
    }

    public static func decode(from data: Data) throws -> SyncFile {
        try JSONDecoder().decode(SyncFile.self, from: data)
    }

    public func encoded() throws -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        return try encoder.encode(self)
    }

    /// Same records, ignoring array order and `version` — used to skip a write when the file on
    /// disk already says exactly what we'd write (avoids touching the mtime, and avoids two
    /// devices rewriting each other's file just because their JSON ordering differs).
    public func isEquivalent(to other: SyncFile) -> Bool {
        func norm<T: SyncRecord>(_ list: [T]) -> [T] { list.sorted { $0.syncId < $1.syncId } }
        return norm(folders) == norm(other.folders) && norm(shortcuts) == norm(other.shortcuts)
    }
}
