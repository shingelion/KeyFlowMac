import AppKit
import ApplicationServices

/// Wraps the macOS Accessibility permission — the same mechanism aText/Espanso/Raycast use to
/// watch and inject keystrokes system-wide. This is the direct analogue of the Android app's
/// AccessibilityService permission.
enum AccessibilityPermission {

    /// Whether this app is currently trusted for accessibility control.
    static var isTrusted: Bool {
        AXIsProcessTrusted()
    }

    /// Triggers the system's own "KeyFlow would like to control this computer" prompt if not
    /// already trusted. This is the sanctioned macOS API for requesting the permission — the user
    /// still has to grant it themselves in System Settings; this only surfaces that dialog.
    @discardableResult
    static func requestPermission() -> Bool {
        let options: [String: Any] = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true]
        return AXIsProcessTrustedWithOptions(options as CFDictionary)
    }

    /// Deep-links to System Settings > Privacy & Security > Accessibility.
    static func openSystemSettings() {
        guard let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility") else { return }
        NSWorkspace.shared.open(url)
    }
}
