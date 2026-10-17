import Foundation
import SwiftData
import KeyFlowCore

/// SwiftData persistence entity for `Folder`, mirroring `ShortcutRecord`'s split between the
/// plain domain struct and the storage-backed model.
@Model
final class FolderRecord {
    @Attribute(.unique) var id: UUID
    var name: String
    var colorTag: String?
    var sortOrder: Int
    var dateCreated: Date
    // Optional, not `Date`: this field was added after folders already existed in the store, and
    // a new non-optional attribute fails SwiftData's automatic lightweight migration (existing
    // rows have no value to migrate to). `asFolder` below falls back to `dateCreated` when nil.
    var dateModified: Date?

    init(from folder: Folder) {
        id = folder.id
        name = folder.name
        colorTag = folder.colorTag
        sortOrder = folder.sortOrder
        dateCreated = folder.dateCreated
        dateModified = folder.dateModified
    }

    /// Local edit (rename, recolor, reorder) — always bumps `dateModified` to "now".
    func apply(_ folder: Folder) {
        name = folder.name
        colorTag = folder.colorTag
        sortOrder = folder.sortOrder
        dateModified = Date()
    }

    /// Applies a sync-merge upsert verbatim, including `dateModified` — never re-stamped with
    /// "now", or future merge comparisons on both devices would drift out of sync.
    func applySynced(_ folder: Folder) {
        name = folder.name
        colorTag = folder.colorTag
        sortOrder = folder.sortOrder
        dateModified = folder.dateModified
    }

    var asFolder: Folder {
        Folder(id: id, name: name, colorTag: colorTag, sortOrder: sortOrder, dateCreated: dateCreated, dateModified: dateModified ?? dateCreated)
    }
}
