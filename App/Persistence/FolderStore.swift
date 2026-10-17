import Foundation
import SwiftData
import KeyFlowCore

/// Single source of truth for folders. Owns the folder-delete policy (move-vs-delete its
/// shortcuts) by calling into `ShortcutStore`, since that decision spans both entities.
@MainActor
final class FolderStore: ObservableObject {

    @Published private(set) var folders: [Folder] = []

    private let context: ModelContext
    private let shortcutStore: ShortcutStore

    init(shortcutStore: ShortcutStore, context: ModelContext) {
        self.shortcutStore = shortcutStore
        self.context = context
        refresh()
    }

    func refresh() {
        let descriptor = FetchDescriptor<FolderRecord>(sortBy: [SortDescriptor(\.name)])
        let records = (try? context.fetch(descriptor)) ?? []
        folders = records.map(\.asFolder)
    }

    func getById(_ id: UUID) -> Folder? {
        folders.first { $0.id == id }
    }

    func getByName(_ name: String) -> Folder? {
        folders.first { $0.name.caseInsensitiveCompare(name) == .orderedSame }
    }

    @discardableResult
    func createFolder(name: String, colorTag: String? = nil) -> Folder {
        let folder = Folder(name: name, colorTag: colorTag, sortOrder: folders.count)
        context.insert(FolderRecord(from: folder))
        save()
        return folder
    }

    /// Resolves a folder name to an id for import, creating the folder if it doesn't exist yet.
    /// Case-insensitive, matching how triggers are matched elsewhere in the app.
    func getOrCreateId(forName name: String) -> UUID {
        if let existing = getByName(name) { return existing.id }
        return createFolder(name: name).id
    }

    func rename(_ folder: Folder, to newName: String) {
        update(folder) { $0.name = newName }
    }

    func setColor(_ folder: Folder, to colorTag: String?) {
        update(folder) { $0.colorTag = colorTag }
    }

    /// Applies a batch of sync-merge upserts — each record's own `dateModified` is kept exactly
    /// as given, never re-stamped with "now" (see `FolderRecord.applySynced`).
    func applySyncedUpserts(_ items: [Folder]) {
        guard !items.isEmpty else { return }
        for folder in items {
            let id = folder.id
            let descriptor = FetchDescriptor<FolderRecord>(predicate: #Predicate { $0.id == id })
            if let existing = try? context.fetch(descriptor).first {
                existing.applySynced(folder)
            } else {
                context.insert(FolderRecord(from: folder))
            }
        }
        save()
    }

    /// Hard-deletes folders by id without touching their shortcuts — used by sync merge, where
    /// the incoming tombstone is authoritative and shortcut reassignment isn't this device's call
    /// to make (the deleting device already resolved that via its own `deleteFolder`, and those
    /// per-shortcut `folderId` changes arrive as their own sync entries).
    func applySyncedDeletes(ids: Set<UUID>) {
        guard !ids.isEmpty else { return }
        for id in ids {
            let descriptor = FetchDescriptor<FolderRecord>(predicate: #Predicate { $0.id == id })
            if let record = try? context.fetch(descriptor).first {
                context.delete(record)
            }
        }
        save()
    }

    func deleteFolder(_ folder: Folder, mode: FolderDeleteMode) {
        switch mode {
        case .moveToUncategorized:
            shortcutStore.unassignFolder(folder.id)
        case .deleteShortcuts:
            shortcutStore.deleteAll(inFolder: folder.id)
        }

        let id = folder.id
        let descriptor = FetchDescriptor<FolderRecord>(predicate: #Predicate { $0.id == id })
        if let record = try? context.fetch(descriptor).first {
            context.delete(record)
            save()
        }
    }

    private func update(_ folder: Folder, mutate: (inout Folder) -> Void) {
        var updated = folder
        mutate(&updated)
        let id = folder.id
        let descriptor = FetchDescriptor<FolderRecord>(predicate: #Predicate { $0.id == id })
        if let record = try? context.fetch(descriptor).first {
            record.apply(updated)
            save()
        }
    }

    private func save() {
        try? context.save()
        refresh()
    }
}
