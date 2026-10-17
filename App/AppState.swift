import Foundation
import Combine
import KeyFlowCore

/// Wires the persistence stores to the keystroke monitor so both the UI and the matching engine
/// stay in sync with the same data, and owns the monitor's start/stop lifecycle.
@MainActor
final class AppState: ObservableObject {

    // `var`, not `let`: SwiftUI's `$appState.settingsStore.someProperty` binding chain (used in
    // SettingsView) needs a ReferenceWritableKeyPath, which only exists for `var` properties —
    // even though the stores themselves are never reassigned after init.
    var shortcutStore: ShortcutStore
    var settingsStore: SettingsStore
    var folderStore: FolderStore
    var syncEngine: SyncEngine

    @Published private(set) var isAccessibilityTrusted = AccessibilityPermission.isTrusted

    private let monitor = KeystrokeMonitor()
    private var cancellables = Set<AnyCancellable>()

    init() {
        // Both stores share one ModelContext so a write through either is immediately visible
        // to the other's next fetch, without relying on cross-context save/merge timing.
        let sharedContext = PersistenceContainer.newContext()
        let shortcutStore = ShortcutStore(context: sharedContext)
        let settingsStore = SettingsStore()
        let folderStore = FolderStore(shortcutStore: shortcutStore, context: sharedContext)
        let tombstoneStore = TombstoneStore()
        let syncEngine = SyncEngine(shortcutStore: shortcutStore, folderStore: folderStore, tombstoneStore: tombstoneStore)
        self.shortcutStore = shortcutStore
        self.settingsStore = settingsStore
        self.folderStore = folderStore
        self.syncEngine = syncEngine

        shortcutStore.$shortcuts
            .sink { [weak self] shortcuts in self?.monitor.updateShortcuts(shortcuts) }
            .store(in: &cancellables)

        settingsStore.objectWillChange
            .sink { [weak self] _ in
                guard let self else { return }
                // objectWillChange fires before the new value is applied; hop to the next
                // run loop turn so the monitor picks up the settings that are about to land.
                DispatchQueue.main.async { self.monitor.updateSettings(self.settingsStore.settings) }
            }
            .store(in: &cancellables)

        // `shortcutStore`/`folderStore`/`settingsStore` are plain properties, not `@Published` —
        // a view that only observes `appState` (via `@EnvironmentObject`) is never notified when
        // one of THEIR `@Published` properties changes, only when AppState's own do. Without this
        // forwarding, a toggle or delete updates the store correctly but the list never re-renders
        // on its own — it only appears to "catch up" when something else (e.g. typing in the
        // search field, which is the view's own local `@State`) happens to force a re-render.
        for publisher in [shortcutStore.objectWillChange, folderStore.objectWillChange, settingsStore.objectWillChange, syncEngine.objectWillChange] {
            publisher
                .sink { [weak self] _ in self?.objectWillChange.send() }
                .store(in: &cancellables)
        }

        monitor.updateShortcuts(shortcutStore.shortcuts)
        monitor.updateSettings(settingsStore.settings)

        tryStartMonitor()

        Task { await syncEngine.performStartupMergeThenWatch() }
    }

    func refreshAccessibilityStatus() {
        isAccessibilityTrusted = AccessibilityPermission.isTrusted
        if isAccessibilityTrusted {
            tryStartMonitor()
        }
    }

    private func tryStartMonitor() {
        guard AccessibilityPermission.isTrusted else { return }
        monitor.start()
    }
}
