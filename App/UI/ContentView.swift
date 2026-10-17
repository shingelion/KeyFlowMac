import SwiftUI

/// Root of the main window: a simple two-tab layout (Shortcuts / Settings), mirroring the
/// Android app's list screen + settings screen reachable from its top bar.
struct RootView: View {
    var body: some View {
        TabView {
            ShortcutListView()
                .tabItem { Label("Shortcuts", systemImage: "list.bullet") }
            SettingsView()
                .tabItem { Label("Settings", systemImage: "gearshape") }
        }
        .frame(minWidth: 480, minHeight: 520)
    }
}
