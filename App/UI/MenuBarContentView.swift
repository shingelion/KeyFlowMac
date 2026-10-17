import SwiftUI

/// Content of the menu bar dropdown — mirrors the Android app's persistent notification: a
/// pause/resume toggle, a quick-add action, and a way back into the main window.
struct MenuBarContentView: View {
    @EnvironmentObject private var appState: AppState
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        if appState.isAccessibilityTrusted {
            Toggle(
                appState.settingsStore.serviceEnabled ? "Expansion Active" : "Expansion Paused",
                isOn: $appState.settingsStore.serviceEnabled
            )
        } else {
            Button("Grant Accessibility Permission…") {
                AccessibilityPermission.requestPermission()
                AccessibilityPermission.openSystemSettings()
            }
        }

        Button("Add Shortcut…") {
            // Without an explicit activate, a .accessory app's new window can open behind
            // whatever else is frontmost, or without key focus, since there's no Dock icon
            // click to have already brought the app forward.
            NSApp.activate(ignoringOtherApps: true)
            openWindow(id: "quickAdd")
        }

        Divider()

        Button("Open KeyFlow") {
            NSApp.activate(ignoringOtherApps: true)
            openWindow(id: "main")
        }

        Divider()

        Button("Quit KeyFlow") { NSApp.terminate(nil) }
            .keyboardShortcut("q")
    }
}
