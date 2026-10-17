import SwiftUI

@main
struct KeyFlowMacApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var appState = AppState()

    var body: some Scene {
        WindowGroup("KeyFlow", id: "main") {
            RootView()
                .environmentObject(appState)
        }
        .defaultSize(width: 560, height: 620)

        Window("Add Shortcut", id: "quickAdd") {
            QuickAddView()
                .environmentObject(appState)
        }
        .windowResizability(.contentSize)

        MenuBarExtra("KeyFlow", systemImage: "keyboard") {
            MenuBarContentView()
                .environmentObject(appState)
        }
    }
}
