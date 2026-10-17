import Foundation

/// A parsed shortcut paired with the folder NAME it referenced in the source file, if any (names,
/// not ids, since ids aren't portable across installs — the importing side resolves or creates a
/// matching folder), and enough of its origin (`rowNumber`, `rawRow`) to build a `SkippedRow` out
/// of it later if `ImportResolver` finds it's a duplicate trigger.
public struct ImportedShortcut {
    public let shortcut: Shortcut
    public let folderName: String?
    public let rowNumber: Int
    public let rawRow: String

    public init(shortcut: Shortcut, folderName: String? = nil, rowNumber: Int, rawRow: String) {
        self.shortcut = shortcut
        self.folderName = folderName
        self.rowNumber = rowNumber
        self.rawRow = rawRow
    }
}

/// A row that didn't make it in, with enough detail to show the user why and to let them export,
/// fix, and re-import it: `rowNumber` and `rawRow` for the "view full row" detail screen, the
/// best-effort structured fields for writing it back out in the app's normal CSV columns, and
/// `reason` naming exactly what went wrong (e.g. "Empty trigger", "Duplicate trigger", "Malformed
/// CSV row").
public struct SkippedRow: Identifiable, Hashable {
    public var id: String { "\(rowNumber)-\(reason)-\(trigger)" }
    public let rowNumber: Int
    public let trigger: String
    public let expansion: String
    public let group: String
    public let folder: String
    public let enabled: String
    public let rawRow: String
    public let reason: String

    public init(
        rowNumber: Int,
        trigger: String,
        expansion: String,
        group: String,
        folder: String,
        enabled: String,
        rawRow: String,
        reason: String
    ) {
        self.rowNumber = rowNumber
        self.trigger = trigger
        self.expansion = expansion
        self.group = group
        self.folder = folder
        self.enabled = enabled
        self.rawRow = rawRow
        self.reason = reason
    }
}

/// Outcome of parsing an import file, before anything is written to the store.
public struct ImportResult {
    public let shortcuts: [ImportedShortcut]
    public let skipped: [SkippedRow]

    public init(shortcuts: [ImportedShortcut], skipped: [SkippedRow] = []) {
        self.shortcuts = shortcuts
        self.skipped = skipped
    }
}
