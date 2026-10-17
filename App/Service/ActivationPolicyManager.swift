import AppKit

/// Applies the "Show in Dock" preference to the app's activation policy. Shared by `AppDelegate`
/// (so the very first launch — including a future Launch-at-Login item, which is just another
/// launch path through the same `applicationDidFinishLaunching`) picks up the saved preference
/// before any window opens, and by `SettingsStore` (so flipping the toggle at runtime takes
/// effect immediately without needing a relaunch).
enum ActivationPolicyManager {

    /// UserDefaults key, shared with `SettingsStore` so both read/write the exact same key.
    static let showInDockKey = "showInDock"

    /// Defaults to hidden (`.accessory`) — KeyFlow is meant to feel like a background utility
    /// (mirroring Espanso/Raycast-style menu-bar tools) whose primary surface is the menu bar
    /// icon, not a Dock presence. Absent any saved preference (first launch), this is `false`.
    static var savedPreference: Bool {
        UserDefaults.standard.object(forKey: showInDockKey) as? Bool ?? false
    }

    /// Sets the activation policy to match `showInDock`. Switching `.accessory` -> `.regular`
    /// can leave the app's windows behind other apps or without key focus, so this also
    /// re-activates the app in that direction; going the other way doesn't need it.
    static func apply(showInDock: Bool) {
        NSApp.setActivationPolicy(showInDock ? .regular : .accessory)
        if showInDock {
            NSApp.activate(ignoringOtherApps: true)
        }
    }
}
