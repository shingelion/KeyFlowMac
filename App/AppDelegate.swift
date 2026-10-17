import AppKit

final class AppDelegate: NSObject, NSApplicationDelegate {
    private let servicesProvider = ServicesProvider()

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Applied here (rather than via a static Info.plist LSUIElement flag) so it reflects the
        // user's live "Show in Dock" preference rather than being hardcoded — this runs on every
        // launch path, including a future Launch-at-Login item, since that's just another way
        // into this same delegate callback.
        ActivationPolicyManager.apply(showInDock: ActivationPolicyManager.savedPreference)

        NSApp.servicesProvider = servicesProvider
        NSUpdateDynamicServices()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }
}
