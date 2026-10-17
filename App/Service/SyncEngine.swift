import AppKit
import Combine
import KeyFlowCore

/// Syncthing-based (or any folder-mirroring tool) cross-device sync via a single JSON file,
/// `keyflow-sync.json`, in a user-chosen folder. The wire format and merge rules match the
/// Android app exactly — see SYNC_PROTOCOL.md at the repo root.
///
/// The one rule everything here is built around: **never write without reading first.** Every
/// path that writes the file — a debounced local edit, "Sync now", startup, enabling sync —
/// goes through `pullThenPush()`, which merges whatever is on disk into local state before
/// deciding what to write, and skips the write entirely if the file already says the same thing.
@MainActor
final class SyncEngine: ObservableObject {

    private enum Keys {
        static let enabled = "syncEnabled"
        static let folderPath = "syncFolderPath"
        static let lastSyncedAt = "syncLastSyncedAt"
        static let explainerShown = "syncExplainerShown"
    }

    private static let syncFileName = "keyflow-sync.json"
    private static let debounceNanoseconds: UInt64 = 2_000_000_000
    private static let pollNanoseconds: UInt64 = 5_000_000_000
    /// An unreadable file modified this recently is probably another device mid-write — wait for
    /// it rather than overwriting it.
    private static let midWriteWindow: TimeInterval = 10

    @Published var isEnabled: Bool {
        didSet {
            defaults.set(isEnabled, forKey: Keys.enabled)
            if isEnabled {
                Task { await performStartupMergeThenWatch() }
            } else {
                debounceTask?.cancel()
                stopWatching()
            }
        }
    }
    @Published private(set) var folderPath: String?
    @Published private(set) var lastSyncedAt: Date?
    @Published private(set) var lastError: String?
    @Published var hasShownExplainer: Bool {
        didSet { defaults.set(hasShownExplainer, forKey: Keys.explainerShown) }
    }

    private let shortcutStore: ShortcutStore
    private let folderStore: FolderStore
    private let tombstoneStore: TombstoneStore
    private let defaults: UserDefaults

    /// Suppresses local-delete tombstone recording while a merge is applying remote changes —
    /// those deletes already carry their own (preserved) tombstone from the incoming file.
    private var isApplyingRemoteMerge = false
    private var lastKnownShortcuts: [UUID: Shortcut] = [:]
    private var lastKnownFolders: [UUID: Folder] = [:]
    private var debounceTask: Task<Void, Never>?
    private var pollTask: Task<Void, Never>?
    private var fileMonitorSource: DispatchSourceFileSystemObject?
    private var watchedFileDescriptor: Int32 = -1
    /// mtime of the sync file the last time we looked at (or wrote) it; a poll only re-reads the
    /// file when this changes.
    private var lastSeenMtime: Date?
    /// Our own last write, so the watcher firing on it isn't re-read as an external change.
    private var lastWrittenData: Data?
    /// Records from the last file we read whose `syncId` we can't key locally (neither a UUID nor
    /// Android's 32-hex form). Never produced by Android, but carried through untouched on write
    /// rather than silently dropped.
    private var preservedShortcuts: [SyncShortcutEntry] = []
    private var preservedFolders: [SyncFolderEntry] = []
    private var cancellables = Set<AnyCancellable>()

    init(shortcutStore: ShortcutStore, folderStore: FolderStore, tombstoneStore: TombstoneStore, defaults: UserDefaults = .standard) {
        self.shortcutStore = shortcutStore
        self.folderStore = folderStore
        self.tombstoneStore = tombstoneStore
        self.defaults = defaults

        isEnabled = defaults.bool(forKey: Keys.enabled)
        folderPath = defaults.string(forKey: Keys.folderPath)
        lastSyncedAt = defaults.object(forKey: Keys.lastSyncedAt) as? Date
        hasShownExplainer = defaults.bool(forKey: Keys.explainerShown)

        lastKnownShortcuts = Dictionary(uniqueKeysWithValues: shortcutStore.shortcuts.map { ($0.id, $0) })
        lastKnownFolders = Dictionary(uniqueKeysWithValues: folderStore.folders.map { ($0.id, $0) })

        shortcutStore.$shortcuts.dropFirst()
            .sink { [weak self] new in self?.handleShortcutsChanged(new) }
            .store(in: &cancellables)
        folderStore.$folders.dropFirst()
            .sink { [weak self] new in self?.handleFoldersChanged(new) }
            .store(in: &cancellables)
    }

    var isConfigured: Bool { folderPath != nil }

    private var syncFileURL: URL? {
        guard let folderPath else { return nil }
        return URL(fileURLWithPath: folderPath, isDirectory: true).appendingPathComponent(Self.syncFileName)
    }

    // MARK: - Folder selection

    func selectFolder() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
        panel.allowsMultipleSelection = false
        panel.prompt = "Choose"
        panel.message = "Choose the same Syncthing-synced folder used on Android."
        guard panel.runModal() == .OK, let url = panel.url else { return }

        stopWatching()
        folderPath = url.path
        defaults.set(url.path, forKey: Keys.folderPath)
        lastSeenMtime = nil
        lastWrittenData = nil
        preservedShortcuts = []
        preservedFolders = []
        Task { await performStartupMergeThenWatch() }
    }

    // MARK: - Entry points

    /// Pull-then-push once, *then* arm the watcher — changes may have arrived while the app was
    /// closed, and they must be merged before anything is written. Runs at launch, and again
    /// whenever sync is turned on or the folder changes.
    func performStartupMergeThenWatch() async {
        guard isEnabled, syncFileURL != nil else { return }
        tombstoneStore.pruneExpired()
        pullThenPush()
        startWatchingIfNeeded()
    }

    func syncNow() {
        guard isEnabled, syncFileURL != nil else { return }
        debounceTask?.cancel()
        pullThenPush()
    }

    private func pullThenPush() {
        pull()
        push()
    }

    // MARK: - Local change observation

    private func handleShortcutsChanged(_ new: [Shortcut]) {
        let newById = Dictionary(uniqueKeysWithValues: new.map { ($0.id, $0) })
        if !isApplyingRemoteMerge {
            let now = SyncTime.ms(Date())
            for (id, old) in lastKnownShortcuts where newById[id] == nil {
                tombstoneStore.recordShortcut(
                    SyncShortcutEntry(old, syncId: tombstoneStore.wireId(for: id),
                                      folderSyncId: old.folderId.map(tombstoneStore.wireId(for:)), deletedAt: now)
                )
            }
        }
        lastKnownShortcuts = newById
        scheduleDebouncedWrite()
    }

    private func handleFoldersChanged(_ new: [Folder]) {
        let newById = Dictionary(uniqueKeysWithValues: new.map { ($0.id, $0) })
        if !isApplyingRemoteMerge {
            let now = SyncTime.ms(Date())
            for (id, old) in lastKnownFolders where newById[id] == nil {
                tombstoneStore.recordFolder(SyncFolderEntry(old, syncId: tombstoneStore.wireId(for: id), deletedAt: now))
            }
        }
        lastKnownFolders = newById
        scheduleDebouncedWrite()
    }

    private func scheduleDebouncedWrite() {
        guard isEnabled, syncFileURL != nil else { return }
        debounceTask?.cancel()
        debounceTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: Self.debounceNanoseconds)
            guard !Task.isCancelled else { return }
            self?.pullThenPush()
        }
    }

    // MARK: - Pull (read + merge)

    private func pull() {
        guard let url = syncFileURL else { return }
        // mtime first, then content: if the file changes in between, the stale mtime just makes
        // the next poll re-read it.
        lastSeenMtime = modificationDate(of: url)
        guard let data = try? Data(contentsOf: url) else { return }
        if data == lastWrittenData { return }
        guard let file = try? SyncFile.decode(from: data) else {
            lastError = "keyflow-sync.json couldn't be read — it may still be syncing. Will retry."
            return
        }
        lastError = nil
        merge(file)
        markSynced()
    }

    private func merge(_ file: SyncFile) {
        isApplyingRemoteMerge = true
        defer { isApplyingRemoteMerge = false }

        adoptMatchingIdentities(from: file)

        preservedFolders = file.folders.filter { tombstoneStore.uuid(forSyncId: $0.syncId) == nil }
        preservedShortcuts = file.shortcuts.filter { tombstoneStore.uuid(forSyncId: $0.syncId) == nil }

        // Folders first — shortcuts reference them.
        var foldersToUpsert: [Folder] = []
        var folderIdsToDelete: Set<UUID> = []
        let folderActions = SyncMerger.resolveAll(
            incoming: file.folders.filter { tombstoneStore.uuid(forSyncId: $0.syncId) != nil },
            localTimestamp: localFolderTimestamp
        )
        for action in folderActions {
            switch action {
            case .upsert(let entry):
                guard let uuid = tombstoneStore.uuid(forSyncId: entry.syncId) else { continue }
                tombstoneStore.bind(syncId: entry.syncId, to: uuid)
                tombstoneStore.clearFolder(entry.syncId)
                foldersToUpsert.append(entry.asFolder(id: uuid))
            case .delete(let entry):
                guard let uuid = tombstoneStore.uuid(forSyncId: entry.syncId) else { continue }
                tombstoneStore.bind(syncId: entry.syncId, to: uuid)
                tombstoneStore.recordFolder(entry)
                folderIdsToDelete.insert(uuid)
            case .ignore:
                break
            }
        }
        if !foldersToUpsert.isEmpty { folderStore.applySyncedUpserts(foldersToUpsert) }
        if !folderIdsToDelete.isEmpty { folderStore.applySyncedDeletes(ids: folderIdsToDelete) }

        var shortcutsToUpsert: [Shortcut] = []
        var shortcutIdsToDelete: Set<UUID> = []
        let shortcutActions = SyncMerger.resolveAll(
            incoming: file.shortcuts.filter { tombstoneStore.uuid(forSyncId: $0.syncId) != nil },
            localTimestamp: localShortcutTimestamp
        )
        for action in shortcutActions {
            switch action {
            case .upsert(let entry):
                guard let uuid = tombstoneStore.uuid(forSyncId: entry.syncId) else { continue }
                tombstoneStore.bind(syncId: entry.syncId, to: uuid)
                tombstoneStore.clearShortcut(entry.syncId)
                shortcutsToUpsert.append(entry.asShortcut(id: uuid, folderId: liveFolderId(forSyncId: entry.folderSyncId)))
            case .delete(let entry):
                guard let uuid = tombstoneStore.uuid(forSyncId: entry.syncId) else { continue }
                tombstoneStore.bind(syncId: entry.syncId, to: uuid)
                tombstoneStore.recordShortcut(entry)
                shortcutIdsToDelete.insert(uuid)
            case .ignore:
                break
            }
        }
        if !shortcutsToUpsert.isEmpty { shortcutStore.applySyncedUpserts(shortcutsToUpsert) }
        if !shortcutIdsToDelete.isEmpty { shortcutStore.delete(ids: shortcutIdsToDelete) }
    }

    /// First-contact reconciliation (see `SyncIdentityAdopter`): a live record in the file that
    /// this device has never seen, matching a local record the file doesn't know about, is the
    /// same record — the local one adopts the file's syncId rather than being duplicated.
    private func adoptMatchingIdentities(from file: SyncFile) {
        let folderIdsInFile = Set(file.folders.map(\.syncId))
        let unknownFolders = file.folders.filter { $0.deletedAt == nil && localFolderTimestamp($0.syncId) == nil }
        let unmatchedLocalFolders = folderStore.folders.filter { !folderIdsInFile.contains(tombstoneStore.wireId(for: $0.id)) }
        for pair in SyncIdentityAdopter.pair(
            incoming: unknownFolders.map { ($0.syncId, $0.name) },
            locals: unmatchedLocalFolders.map { ($0.id, $0.name) }
        ) {
            tombstoneStore.bind(syncId: pair.syncId, to: pair.localId)
        }

        let shortcutIdsInFile = Set(file.shortcuts.map(\.syncId))
        let unknownShortcuts = file.shortcuts.filter { $0.deletedAt == nil && localShortcutTimestamp($0.syncId) == nil }
        let unmatchedLocalShortcuts = shortcutStore.shortcuts.filter { !shortcutIdsInFile.contains(tombstoneStore.wireId(for: $0.id)) }
        for pair in SyncIdentityAdopter.pair(
            incoming: unknownShortcuts.map { ($0.syncId, $0.trigger) },
            locals: unmatchedLocalShortcuts.map { ($0.id, $0.trigger) }
        ) {
            tombstoneStore.bind(syncId: pair.syncId, to: pair.localId)
        }
    }

    private func localShortcutTimestamp(_ syncId: String) -> Int64? {
        if let uuid = tombstoneStore.uuid(forSyncId: syncId), let live = shortcutStore.getById(uuid) {
            return SyncTime.ms(live.dateModified)
        }
        return tombstoneStore.shortcutTimestamp(syncId)
    }

    private func localFolderTimestamp(_ syncId: String) -> Int64? {
        if let uuid = tombstoneStore.uuid(forSyncId: syncId), let live = folderStore.getById(uuid) {
            return SyncTime.ms(live.dateModified)
        }
        return tombstoneStore.folderTimestamp(syncId)
    }

    /// A shortcut pointing at a folder we don't have (or that was deleted) lands in Uncategorized,
    /// like Android's `folderSyncIdToLocalId[it]` lookup returning null.
    private func liveFolderId(forSyncId syncId: String?) -> UUID? {
        guard let syncId, let uuid = tombstoneStore.uuid(forSyncId: syncId), folderStore.getById(uuid) != nil else { return nil }
        return uuid
    }

    // MARK: - Push (write)

    private func buildLocalFile() -> SyncFile {
        let wire = tombstoneStore.wireId(for:)

        let liveFolders = folderStore.folders.map { SyncFolderEntry($0, syncId: wire($0.id)) }
        let liveFolderIds = Set(liveFolders.map(\.syncId))
        let folders = liveFolders
            + tombstoneStore.folderEntries.filter { !liveFolderIds.contains($0.syncId) }
            + preservedFolders

        let liveShortcuts = shortcutStore.shortcuts.map {
            SyncShortcutEntry($0, syncId: wire($0.id), folderSyncId: $0.folderId.map(wire))
        }
        let liveShortcutIds = Set(liveShortcuts.map(\.syncId))
        let shortcuts = liveShortcuts
            + tombstoneStore.shortcutEntries.filter { !liveShortcutIds.contains($0.syncId) }
            + preservedShortcuts

        return SyncFile(
            folders: folders.sorted { $0.syncId < $1.syncId },
            shortcuts: shortcuts.sorted { $0.syncId < $1.syncId }
        )
    }

    private func push() {
        guard let url = syncFileURL else { return }
        let file = buildLocalFile()

        if let existingData = try? Data(contentsOf: url) {
            if let existing = try? SyncFile.decode(from: existingData) {
                // The file already says exactly this — don't touch it.
                if existing.isEquivalent(to: file) {
                    markSynced()
                    return
                }
            } else if let mtime = modificationDate(of: url), Date().timeIntervalSince(mtime) < Self.midWriteWindow {
                lastError = "keyflow-sync.json is being written by another device — waiting."
                scheduleDebouncedWrite()
                return
            }
        }

        do {
            try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            let data = try file.encoded()
            lastWrittenData = data
            try data.write(to: url, options: .atomic)
            lastSeenMtime = modificationDate(of: url)
            lastError = nil
            markSynced()
        } catch {
            lastError = error.localizedDescription
        }
    }

    private func markSynced() {
        lastSyncedAt = Date()
        defaults.set(lastSyncedAt, forKey: Keys.lastSyncedAt)
    }

    private func modificationDate(of url: URL) -> Date? {
        (try? FileManager.default.attributesOfItem(atPath: url.path))?[.modificationDate] as? Date
    }

    // MARK: - Watching

    /// Two mechanisms, because neither alone is reliable:
    ///  - A `DispatchSource` on the containing folder fires immediately when a file is added or
    ///    renamed into place (what Syncthing does).
    ///  - A cheap mtime poll catches a file modified **in place**, which never touches the folder's
    ///    entries and so is invisible to the watcher (what Android's writer does).
    private func startWatchingIfNeeded() {
        guard fileMonitorSource == nil, let url = syncFileURL else { return }

        let folderURL = url.deletingLastPathComponent()
        let fd = open(folderURL.path, O_EVTONLY)
        if fd >= 0 {
            watchedFileDescriptor = fd
            let source = DispatchSource.makeFileSystemObjectSource(fileDescriptor: fd, eventMask: [.write], queue: .main)
            source.setEventHandler { [weak self] in self?.pollIfChanged() }
            source.setCancelHandler { [weak self] in
                if let fd = self?.watchedFileDescriptor, fd >= 0 { close(fd) }
            }
            source.resume()
            fileMonitorSource = source
        }

        pollTask?.cancel()
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: Self.pollNanoseconds)
                self?.pollIfChanged()
            }
        }
    }

    private func stopWatching() {
        fileMonitorSource?.cancel()
        fileMonitorSource = nil
        pollTask?.cancel()
        pollTask = nil
    }

    /// Re-reads the file only if its mtime moved since we last looked. Merging changes local
    /// state, which schedules its own debounced pull-then-push if there's anything to send back.
    private func pollIfChanged() {
        guard isEnabled, let url = syncFileURL else { return }
        let mtime = modificationDate(of: url)
        guard mtime != lastSeenMtime else { return }
        if mtime == nil {
            // The file disappeared (deleted, or mid-replace) — recreate it from local state.
            lastSeenMtime = nil
            scheduleDebouncedWrite()
            return
        }
        pull()
    }
}
