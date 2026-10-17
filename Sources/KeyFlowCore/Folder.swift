import Foundation

/// A user-defined group for organizing shortcuts. Shortcuts with no folder show up in the
/// implicit "Uncategorized" view rather than belonging to a real Folder row.
public struct Folder: Identifiable, Codable, Hashable {
    public var id: UUID
    public var name: String
    public var colorTag: String?
    public var sortOrder: Int
    public var dateCreated: Date
    public var dateModified: Date

    public init(
        id: UUID = UUID(),
        name: String,
        colorTag: String? = nil,
        sortOrder: Int = 0,
        dateCreated: Date = Date(),
        dateModified: Date = Date()
    ) {
        self.id = id
        self.name = name
        self.colorTag = colorTag
        self.sortOrder = sortOrder
        self.dateCreated = dateCreated
        self.dateModified = dateModified
    }
}

/// What to do with a folder's shortcuts when the folder itself is deleted.
public enum FolderDeleteMode {
    case moveToUncategorized
    case deleteShortcuts
}

/// A palette of distinct, pleasant colors for folder color tags (hex strings), shared by any UI
/// that lets the user pick one.
public let folderColorPalette: [String] = [
    "#E53935", "#FB8C00", "#FDD835", "#43A047", "#00ACC1", "#1E88E5", "#8E24AA", "#6D4C41",
]
