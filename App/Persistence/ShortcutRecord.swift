import Foundation
import SwiftData
import KeyFlowCore

/// SwiftData persistence entity. Kept separate from the plain `Shortcut` struct that
/// ExpansionEngine/importers/exporters operate on, so the matching engine and the CSV/JSON
/// import-export code (in the KeyFlowCore package) never need to know SwiftData exists.
@Model
final class ShortcutRecord {
    @Attribute(.unique) var id: UUID
    var trigger: String
    var expansion: String
    var isRichText: Bool
    var group: String?
    var enabled: Bool
    var dateCreated: Date
    var dateModified: Date
    /// nil means "Uncategorized" — no FolderRecord row is created for it.
    var folderId: UUID?

    init(from shortcut: Shortcut) {
        id = shortcut.id
        trigger = shortcut.trigger
        expansion = shortcut.expansion
        isRichText = shortcut.isRichText
        group = shortcut.group
        enabled = shortcut.enabled
        dateCreated = shortcut.dateCreated
        dateModified = shortcut.dateModified
        folderId = shortcut.folderId
    }

    /// Local edit — always bumps `dateModified` to "now".
    func apply(_ shortcut: Shortcut) {
        trigger = shortcut.trigger
        expansion = shortcut.expansion
        isRichText = shortcut.isRichText
        group = shortcut.group
        enabled = shortcut.enabled
        folderId = shortcut.folderId
        dateModified = Date()
    }

    /// Applies a sync-merge upsert verbatim, including `dateModified` — never re-stamped with
    /// "now", or future merge comparisons on both devices would drift out of sync.
    func applySynced(_ shortcut: Shortcut) {
        trigger = shortcut.trigger
        expansion = shortcut.expansion
        isRichText = shortcut.isRichText
        group = shortcut.group
        enabled = shortcut.enabled
        folderId = shortcut.folderId
        dateModified = shortcut.dateModified
    }

    var asShortcut: Shortcut {
        Shortcut(
            id: id,
            trigger: trigger,
            expansion: expansion,
            isRichText: isRichText,
            group: group,
            enabled: enabled,
            dateCreated: dateCreated,
            dateModified: dateModified,
            folderId: folderId
        )
    }
}
