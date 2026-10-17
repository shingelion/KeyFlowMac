import Foundation

/// A trigger found immediately before the cursor, ready to be replaced.
public struct ExpansionMatch: Equatable {
    public let shortcut: Shortcut
    /// Start offset (in characters) of the trigger text within the field, inclusive.
    public let matchStart: Int
    /// End offset (in characters) of the trigger text within the field, exclusive.
    public let matchEnd: Int
    /// The exact substring the user typed (used to preserve/adapt case).
    public let typedTrigger: String
}

/// In-memory trigger matcher. Holds the current set of enabled shortcuts and matches the text
/// immediately before the cursor against it on every keystroke. Kept independent of any
/// persistence mechanism so it can run against a plain in-memory keystroke buffer without
/// touching disk on the hot path — mirrors the Android version's ExpansionEngine exactly.
public final class ExpansionEngine {

    private var triggersByLengthDesc: [String] = []
    private var shortcutsByLowerTrigger: [String: Shortcut] = [:]

    public init() {}

    public func updateShortcuts(_ shortcuts: [Shortcut]) {
        let enabled = shortcuts.filter { $0.enabled }
        shortcutsByLowerTrigger = Dictionary(enabled.map { ($0.trigger.lowercased(), $0) }, uniquingKeysWith: { _, new in new })
        triggersByLengthDesc = Array(Set(enabled.map { $0.trigger })).sorted { $0.count > $1.count }
    }

    /// - Parameter textBeforeCursor: all text in the field up to (not including) the caret.
    public func findMatch(textBeforeCursor: String, settings: AppSettings) -> ExpansionMatch? {
        guard !triggersByLengthDesc.isEmpty, !textBeforeCursor.isEmpty else { return nil }

        let chars = Array(textBeforeCursor)
        let searchChars: [Character]
        switch settings.expansionKey {
        case .boundaryKey:
            guard let last = chars.last, isBoundaryChar(last) else { return nil }
            searchChars = Array(chars.dropLast())
        case .immediate:
            searchChars = chars
        }
        guard !searchChars.isEmpty else { return nil }

        for trigger in triggersByLengthDesc {
            let triggerChars = Array(trigger)
            guard searchChars.count >= triggerChars.count else { continue }
            let candidateStart = searchChars.count - triggerChars.count
            let candidateChars = Array(searchChars[candidateStart...])
            let candidate = String(candidateChars)

            let matches = settings.caseSensitive
                ? candidate == trigger
                : candidate.lowercased() == trigger.lowercased()
            guard matches else { continue }

            let precedingChar: Character? = candidateStart > 0 ? searchChars[candidateStart - 1] : nil
            let boundaryOk: Bool
            if !settings.triggerPrefix.isEmpty {
                boundaryOk = precedingChar != nil && String(precedingChar!) == settings.triggerPrefix
            } else {
                boundaryOk = precedingChar == nil || !precedingChar!.isLetter && !precedingChar!.isNumber
            }
            guard boundaryOk else { continue }

            guard let shortcut = shortcutsByLowerTrigger[trigger.lowercased()] else { continue }
            return ExpansionMatch(
                shortcut: shortcut,
                matchStart: candidateStart,
                matchEnd: candidateStart + triggerChars.count,
                typedTrigger: candidate
            )
        }
        return nil
    }

    private func isBoundaryChar(_ c: Character) -> Bool {
        if c == " " || c == "\t" || c == "\n" { return true }
        return ".,;:!?)]}\"'".contains(c)
    }
}
