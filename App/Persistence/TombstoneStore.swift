import Foundation
import KeyFlowCore

/// Sync bookkeeping that doesn't belong in the main SwiftData store:
///
/// 1. **Tombstones** — locally-known deletions (user-initiated and merged in from a remote
///    tombstone), keyed by wire `syncId`, so writes can include them. Stores full
///    `SyncShortcutEntry`/`SyncFolderEntry` snapshots so a written tombstone keeps its last
///    known field values.
/// 2. **Wire-id bindings** — the Mac keys records by `UUID`, but a syncId that arrived as
///    Android's 32-hex legacy form (or any non-canonical spelling), or that a local record
///    *adopted* on first contact, must be written back *exactly* as received, or Android would
///    see a new syncId and duplicate the record. Stored both ways so it resolves either direction.
///
/// UserDefaults-backed: the volume is small and it sidesteps a SwiftData schema migration.
@MainActor
final class TombstoneStore: ObservableObject {
    private static let shortcutsKey = "syncShortcutTombstonesV2"
    private static let foldersKey = "syncFolderTombstonesV2"
    private static let overridesKey = "syncWireIdOverrides"
    private static let maxAge: TimeInterval = 30 * 24 * 3600

    private(set) var shortcutTombstones: [String: SyncShortcutEntry] = [:]
    private(set) var folderTombstones: [String: SyncFolderEntry] = [:]
    /// UUID string -> the exact wire syncId to use for it, only where that differs from canonical.
    private var wireIdOverrides: [String: String] = [:]
    /// The reverse: a bound wire syncId -> the local UUID it belongs to.
    private var uuidByWireId: [String: UUID] = [:]

    private let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        load()
    }

    // MARK: Wire ids

    func wireId(for uuid: UUID) -> String {
        wireIdOverrides[uuid.uuidString] ?? SyncIdMapper.canonicalSyncId(for: uuid)
    }

    /// The local UUID for a wire syncId: an explicit binding (adopted or non-canonical id) wins,
    /// otherwise it's derived from the id's own UUID/hex form. `nil` if it's neither.
    func uuid(forSyncId syncId: String) -> UUID? {
        uuidByWireId[syncId] ?? SyncIdMapper.parse(syncId)?.uuid
    }

    /// Binds `syncId` to `uuid` so it's written back verbatim and resolves to `uuid` on the way in.
    /// A no-op when the id is already just `uuid`'s canonical form.
    func bind(syncId: String, to uuid: UUID) {
        guard syncId != SyncIdMapper.canonicalSyncId(for: uuid) || uuidByWireId[syncId] != nil else { return }
        guard wireIdOverrides[uuid.uuidString] != syncId || uuidByWireId[syncId] != uuid else { return }
        wireIdOverrides[uuid.uuidString] = syncId
        uuidByWireId[syncId] = uuid
        defaults.set(wireIdOverrides, forKey: Self.overridesKey)
    }

    // MARK: Tombstones

    func shortcutTimestamp(_ syncId: String) -> Int64? { shortcutTombstones[syncId]?.effectiveTimestamp }
    func folderTimestamp(_ syncId: String) -> Int64? { folderTombstones[syncId]?.effectiveTimestamp }

    var shortcutEntries: [SyncShortcutEntry] { Array(shortcutTombstones.values) }
    var folderEntries: [SyncFolderEntry] { Array(folderTombstones.values) }

    /// Records/updates a tombstone, keeping whichever is newer if one already exists.
    func recordShortcut(_ entry: SyncShortcutEntry) {
        guard entry.deletedAt != nil else { return }
        if let existing = shortcutTombstones[entry.syncId], existing.effectiveTimestamp >= entry.effectiveTimestamp { return }
        shortcutTombstones[entry.syncId] = entry
        saveShortcuts()
    }

    func recordFolder(_ entry: SyncFolderEntry) {
        guard entry.deletedAt != nil else { return }
        if let existing = folderTombstones[entry.syncId], existing.effectiveTimestamp >= entry.effectiveTimestamp { return }
        folderTombstones[entry.syncId] = entry
        saveFolders()
    }

    /// Clears a tombstone once a newer live record supersedes it.
    func clearShortcut(_ syncId: String) {
        guard shortcutTombstones.removeValue(forKey: syncId) != nil else { return }
        saveShortcuts()
    }

    func clearFolder(_ syncId: String) {
        guard folderTombstones.removeValue(forKey: syncId) != nil else { return }
        saveFolders()
    }

    /// Drops tombstones older than 30 days. Call once at launch.
    func pruneExpired() {
        let cutoff = SyncTime.ms(Date().addingTimeInterval(-Self.maxAge))
        let beforeS = shortcutTombstones.count
        shortcutTombstones = shortcutTombstones.filter { $0.value.effectiveTimestamp >= cutoff }
        if shortcutTombstones.count != beforeS { saveShortcuts() }

        let beforeF = folderTombstones.count
        folderTombstones = folderTombstones.filter { $0.value.effectiveTimestamp >= cutoff }
        if folderTombstones.count != beforeF { saveFolders() }
    }

    // MARK: Persistence

    private func load() {
        if let data = defaults.data(forKey: Self.shortcutsKey),
           let entries = try? JSONDecoder().decode([SyncShortcutEntry].self, from: data) {
            shortcutTombstones = Dictionary(entries.map { ($0.syncId, $0) }, uniquingKeysWith: { a, _ in a })
        }
        if let data = defaults.data(forKey: Self.foldersKey),
           let entries = try? JSONDecoder().decode([SyncFolderEntry].self, from: data) {
            folderTombstones = Dictionary(entries.map { ($0.syncId, $0) }, uniquingKeysWith: { a, _ in a })
        }
        wireIdOverrides = (defaults.dictionary(forKey: Self.overridesKey) as? [String: String]) ?? [:]
        for (uuidString, wire) in wireIdOverrides {
            if let uuid = UUID(uuidString: uuidString) { uuidByWireId[wire] = uuid }
        }
    }

    private func saveShortcuts() {
        guard let data = try? JSONEncoder().encode(Array(shortcutTombstones.values)) else { return }
        defaults.set(data, forKey: Self.shortcutsKey)
    }

    private func saveFolders() {
        guard let data = try? JSONEncoder().encode(Array(folderTombstones.values)) else { return }
        defaults.set(data, forKey: Self.foldersKey)
    }
}
