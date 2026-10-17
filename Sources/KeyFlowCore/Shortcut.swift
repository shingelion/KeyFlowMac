import Foundation

/// A single trigger -> expansion mapping. Pure value type shared by the matching engine,
/// importers/exporters, and the app's persistence layer (which maps it to/from a SwiftData
/// model) — none of the matching logic needs to know anything about how shortcuts are stored.
public struct Shortcut: Identifiable, Codable, Hashable {
    public var id: UUID
    public var trigger: String
    public var expansion: String
    public var isRichText: Bool
    public var group: String?
    public var enabled: Bool
    public var dateCreated: Date
    public var dateModified: Date
    /// nil means "Uncategorized" — no real Folder row, just the implicit default view.
    public var folderId: UUID?

    public init(
        id: UUID = UUID(),
        trigger: String,
        expansion: String,
        isRichText: Bool = false,
        group: String? = nil,
        enabled: Bool = true,
        dateCreated: Date = Date(),
        dateModified: Date = Date(),
        folderId: UUID? = nil
    ) {
        self.id = id
        self.trigger = trigger
        self.expansion = expansion
        self.isRichText = isRichText
        self.group = group
        self.enabled = enabled
        self.dateCreated = dateCreated
        self.dateModified = dateModified
        self.folderId = folderId
    }
}
