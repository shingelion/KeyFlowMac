import AppKit
import ApplicationServices
import KeyFlowCore

/// Watches keystrokes system-wide via a CGEventTap (the macOS equivalent of Android's
/// AccessibilityService watching TYPE_VIEW_TEXT_CHANGED) — this is how aText/Espanso/Raycast do
/// text expansion on macOS too. Because macOS has no generic "read the current field's text" API
/// the way AccessibilityNodeInfo does, this maintains its own in-memory buffer of recently typed
/// characters instead, cleared whenever the buffer can no longer be trusted (non-character keys,
/// a mouse click, or the focused app changing).
///
/// On a match, the trigger is deleted with synthetic backspaces and the expansion is typed back
/// in as synthetic Unicode keystrokes — never touching the clipboard. Synthetic events are
/// tagged so the tap's own callback ignores them instead of re-processing its own output.
@MainActor
final class KeystrokeMonitor {

    private let engine = ExpansionEngine()
    private var settings = AppSettings()

    private var eventTap: CFMachPort?
    private var runLoopSource: CFRunLoopSource?
    private var buffer: String = ""

    private nonisolated static let syntheticEventMarker: Int64 = 0x54_58_50_4E_44 // "TXPND" tag on injected events
    private let maxBufferLength = 256

    private var isRunning = false
    private var activationObserver: NSObjectProtocol?

    init() {
        activationObserver = NotificationCenter.default.addObserver(
            forName: NSWorkspace.didActivateApplicationNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in self?.buffer = "" }
        }
    }

    deinit {
        if let activationObserver {
            NotificationCenter.default.removeObserver(activationObserver)
        }
    }

    func updateShortcuts(_ shortcuts: [Shortcut]) {
        engine.updateShortcuts(shortcuts)
    }

    func updateSettings(_ settings: AppSettings) {
        self.settings = settings
    }

    @discardableResult
    func start() -> Bool {
        guard !isRunning else { return true }
        guard AccessibilityPermission.isTrusted else { return false }

        let eventMask = (1 << CGEventType.keyDown.rawValue) | (1 << CGEventType.leftMouseDown.rawValue)
        let callback: CGEventTapCallBack = { _, type, event, refcon in
            guard let refcon else { return Unmanaged.passUnretained(event) }
            let monitor = Unmanaged<KeystrokeMonitor>.fromOpaque(refcon).takeUnretainedValue()
            return monitor.handle(type: type, event: event)
        }

        guard let tap = CGEvent.tapCreate(
            tap: .cgSessionEventTap,
            place: .headInsertEventTap,
            options: .defaultTap,
            eventsOfInterest: CGEventMask(eventMask),
            callback: callback,
            userInfo: Unmanaged.passUnretained(self).toOpaque()
        ) else {
            return false
        }

        eventTap = tap
        let source = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, tap, 0)
        runLoopSource = source
        CFRunLoopAddSource(CFRunLoopGetCurrent(), source, .commonModes)
        CGEvent.tapEnable(tap: tap, enable: true)
        isRunning = true
        return true
    }

    func stop() {
        guard isRunning, let tap = eventTap else { return }
        CGEvent.tapEnable(tap: tap, enable: false)
        if let source = runLoopSource {
            CFRunLoopRemoveSource(CFRunLoopGetCurrent(), source, .commonModes)
        }
        eventTap = nil
        runLoopSource = nil
        isRunning = false
    }

    private nonisolated func handle(type: CGEventType, event: CGEvent) -> Unmanaged<CGEvent>? {
        // Ignore events we synthesized ourselves to avoid re-matching our own output.
        if event.getIntegerValueField(.eventSourceUserData) == Self.syntheticEventMarker {
            return Unmanaged.passUnretained(event)
        }

        if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput {
            MainActor.assumeIsolated {
                if let tap = self.eventTap { CGEvent.tapEnable(tap: tap, enable: true) }
            }
            return Unmanaged.passUnretained(event)
        }

        if type == .leftMouseDown {
            MainActor.assumeIsolated { self.buffer = "" }
            return Unmanaged.passUnretained(event)
        }

        guard type == .keyDown else { return Unmanaged.passUnretained(event) }
        return MainActor.assumeIsolated {
            self.handleKeyDown(event)
            return Unmanaged.passUnretained(event)
        }
    }

    private func handleKeyDown(_ event: CGEvent) {
        guard settings.serviceEnabled else { return }
        guard !isFocusedElementSecure() else { buffer = ""; return }

        let keyCode = event.getIntegerValueField(.keyboardEventKeycode)
        if isDeleteKey(keyCode) {
            if !buffer.isEmpty { buffer.removeLast() }
            return
        }
        if isNonBufferingKey(keyCode) {
            buffer = ""
            return
        }

        var length = 0
        var chars = [UniChar](repeating: 0, count: 8)
        event.keyboardGetUnicodeString(maxStringLength: 8, actualStringLength: &length, unicodeString: &chars)
        guard length > 0 else { return }
        let typed = String(utf16CodeUnits: chars, count: length)
        guard typed.unicodeScalars.allSatisfy({ $0.properties.generalCategory != .control }) else {
            buffer = ""
            return
        }

        buffer.append(typed)
        if buffer.count > maxBufferLength {
            buffer = String(buffer.suffix(maxBufferLength))
        }

        guard let match = engine.findMatch(textBeforeCursor: buffer, settings: settings) else { return }

        let processed = PlaceholderProcessor.process(match.shortcut.expansion)
        var finalText = processed.text
        if !settings.caseSensitive && settings.preserveCase {
            finalText = CaseAdapter.apply(typedTrigger: match.typedTrigger, expansion: finalText)
        }

        performExpansion(deleteCount: match.matchEnd - match.matchStart, insert: finalText)
        buffer = ""
    }

    /// Best-effort check for a secure (password) field, using the system-wide focused element —
    /// the closest macOS equivalent to Android's `AccessibilityNodeInfo.isPassword`.
    private func isFocusedElementSecure() -> Bool {
        let systemWide = AXUIElementCreateSystemWide()
        var focused: AnyObject?
        guard AXUIElementCopyAttributeValue(systemWide, kAXFocusedUIElementAttribute as CFString, &focused) == .success,
              let element = focused else {
            return false
        }
        var role: AnyObject?
        AXUIElementCopyAttributeValue(element as! AXUIElement, kAXRoleAttribute as CFString, &role)
        if (role as? String) == "AXSecureTextField" { return true }

        var subrole: AnyObject?
        AXUIElementCopyAttributeValue(element as! AXUIElement, kAXSubroleAttribute as CFString, &subrole)
        return (subrole as? String) == "AXSecureTextField"
    }

    private func isDeleteKey(_ keyCode: Int64) -> Bool {
        keyCode == 51 // kVK_Delete (backspace)
    }

    /// Arrow keys, escape, function/navigation keys, tab — anything that moves the caret out from
    /// under our buffer without producing a character we can track.
    private func isNonBufferingKey(_ keyCode: Int64) -> Bool {
        let nonBuffering: Set<Int64> = [
            123, 124, 125, 126, // arrow keys
            53,                 // escape
            48,                 // tab
            116, 121, 115, 119, // page up/down, home, end
            117,                // forward delete
        ]
        return nonBuffering.contains(keyCode)
    }

    private func performExpansion(deleteCount: Int, insert: String) {
        let source = CGEventSource(stateID: .hidSystemState)

        for _ in 0..<deleteCount {
            postSyntheticKey(source: source, keyCode: 51, keyDown: true)
            postSyntheticKey(source: source, keyCode: 51, keyDown: false)
        }

        guard let down = CGEvent(keyboardEventSource: source, virtualKey: 0, keyDown: true) else { return }
        let utf16 = Array(insert.utf16)
        down.setIntegerValueField(.eventSourceUserData, value: Self.syntheticEventMarker)
        down.keyboardSetUnicodeString(stringLength: utf16.count, unicodeString: utf16)
        down.post(tap: .cgSessionEventTap)

        guard let up = CGEvent(keyboardEventSource: source, virtualKey: 0, keyDown: false) else { return }
        up.setIntegerValueField(.eventSourceUserData, value: Self.syntheticEventMarker)
        up.keyboardSetUnicodeString(stringLength: utf16.count, unicodeString: utf16)
        up.post(tap: .cgSessionEventTap)
    }

    private func postSyntheticKey(source: CGEventSource?, keyCode: CGKeyCode, keyDown: Bool) {
        guard let event = CGEvent(keyboardEventSource: source, virtualKey: keyCode, keyDown: keyDown) else { return }
        event.setIntegerValueField(.eventSourceUserData, value: Self.syntheticEventMarker)
        event.post(tap: .cgSessionEventTap)
    }
}
