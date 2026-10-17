import AppKit

/// Backs the "Add as KeyFlow Shortcut" item that appears in the system Services menu when
/// text is selected in any app — the macOS equivalent of Android's "Process text" share action.
/// Registered via NSServices in Info.plist; NSApplication routes matching selections here.
final class ServicesProvider: NSObject {

    /// Called by AppKit; the selector name must match the `NSMessage` key in Info.plist exactly.
    @objc func addAsShortcut(_ pasteboard: NSPasteboard, userData: String, error: AutoreleasingUnsafeMutablePointer<NSString>) {
        guard let types = pasteboard.types, types.contains(.string),
              let text = pasteboard.string(forType: .string), !text.isEmpty else {
            error.pointee = "No text was selected." as NSString
            return
        }
        NotificationCenter.default.post(name: .keyFlowAddAsShortcut, object: nil, userInfo: ["text": text])
    }
}

extension Notification.Name {
    static let keyFlowAddAsShortcut = Notification.Name("com.keyflow.mac.addAsShortcut")
}
