import Foundation
import SwiftData
import Combine
import KeyFlowCore

/// Single source of truth for shortcuts, shared by the SwiftUI views and the keystroke monitor.
/// All writes funnel through here so `$shortcuts` is one place both sides can observe — SwiftUI
/// via `@ObservedObject`/`@EnvironmentObject`, the matching engine via a Combine `sink` — without
/// needing to reach into SwiftData's own change notifications from outside a View.
@MainActor
final class ShortcutStore: ObservableObject {

    @Published private(set) var shortcuts: [Shortcut] = []

    private let context: ModelContext

    init(context: ModelContext) {
        self.context = context
        refresh()
    }

    func refresh() {
        let descriptor = FetchDescriptor<ShortcutRecord>(sortBy: [SortDescriptor(\.trigger)])
        let records = (try? context.fetch(descriptor)) ?? []
        shortcuts = records.map(\.asShortcut)
    }

    var groups: [String] {
        Array(Set(shortcuts.compactMap(\.group))).sorted()
    }

    func getByTrigger(_ trigger: String) -> Shortcut? {
        shortcuts.first { $0.trigger.caseInsensitiveCompare(trigger) == .orderedSame }
    }

    func getById(_ id: UUID) -> Shortcut? {
        shortcuts.first { $0.id == id }
    }

    /// Count of enabled+disabled shortcuts currently filed under a given folder.
    func count(inFolder folderId: UUID) -> Int {
        shortcuts.count { $0.folderId == folderId }
    }

    @discardableResult
    func upsert(_ shortcut: Shortcut) -> Shortcut {
        upsertWithoutSaving(shortcut)
        save()
        return shortcut
    }

    func delete(_ shortcut: Shortcut) {
        deleteWithoutSaving(id: shortcut.id)
        save()
    }

    func delete(ids: Set<UUID>) {
        guard !ids.isEmpty else { return }
        for id in ids { deleteWithoutSaving(id: id) }
        save()
    }

    /// Applies a batch of sync-merge upserts — each record's own `dateModified` is kept exactly
    /// as given, never re-stamped with "now" (see `ShortcutRecord.applySynced`).
    func applySyncedUpserts(_ items: [Shortcut]) {
        guard !items.isEmpty else { return }
        for shortcut in items {
            let id = shortcut.id
            let descriptor = FetchDescriptor<ShortcutRecord>(predicate: #Predicate { $0.id == id })
            if let existing = try? context.fetch(descriptor).first {
                existing.applySynced(shortcut)
            } else {
                context.insert(ShortcutRecord(from: shortcut))
            }
        }
        save()
    }

    /// Reassigns every shortcut with one of `ids` to `folderId` (nil moves them to Uncategorized).
    func moveToFolder(ids: Set<UUID>, folderId: UUID?) {
        guard !ids.isEmpty else { return }
        for id in ids {
            let descriptor = FetchDescriptor<ShortcutRecord>(predicate: #Predicate { $0.id == id })
            if let record = try? context.fetch(descriptor).first {
                record.folderId = folderId
                record.dateModified = Date()
            }
        }
        save()
    }

    /// Every shortcut currently filed under `folderId` moves to Uncategorized — used when a
    /// folder is deleted with "move to Uncategorized" rather than "delete shortcuts too".
    func unassignFolder(_ folderId: UUID) {
        let affected = shortcuts.filter { $0.folderId == folderId }.map(\.id)
        moveToFolder(ids: Set(affected), folderId: nil)
    }

    /// Deletes every shortcut currently filed under `folderId` — the other half of the
    /// delete-folder choice.
    func deleteAll(inFolder folderId: UUID) {
        let affected = Set(shortcuts.filter { $0.folderId == folderId }.map(\.id))
        delete(ids: affected)
    }

    /// Bulk import: shortcuts are upserted by id, matching `ImportResolver`'s already-resolved
    /// list (new shortcuts get a fresh id; overwritten duplicates already carry the existing
    /// record's id). Returns the number written.
    @discardableResult
    func importAll(_ resolved: [Shortcut]) -> Int {
        guard !resolved.isEmpty else { return 0 }
        for shortcut in resolved {
            upsertWithoutSaving(shortcut)
        }
        save()
        return resolved.count
    }

    private func upsertWithoutSaving(_ shortcut: Shortcut) {
        let id = shortcut.id
        let descriptor = FetchDescriptor<ShortcutRecord>(predicate: #Predicate { $0.id == id })
        if let existing = try? context.fetch(descriptor).first {
            existing.apply(shortcut)
        } else {
            context.insert(ShortcutRecord(from: shortcut))
        }
    }

    private func deleteWithoutSaving(id: UUID) {
        let descriptor = FetchDescriptor<ShortcutRecord>(predicate: #Predicate { $0.id == id })
        let found = (try? context.fetch(descriptor)) ?? []
        if let existing = found.first {
            context.delete(existing)
        }
    }

    private func save() {
        do {
            try context.save()
        } catch {
            print("ShortcutStore: context.save() failed: \(error)")
        }
        refresh()
    }
}
