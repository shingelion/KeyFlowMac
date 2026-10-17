import Foundation

/// When to fire an expansion once a trigger is matched.
public enum ExpansionKey: String, CaseIterable, Codable {
    /// Expand as soon as the trigger text matches, without waiting for a boundary key.
    case immediate
    /// Wait for space/tab/return/punctuation after the trigger before expanding.
    case boundaryKey
}

public struct AppSettings: Codable, Equatable {
    public var serviceEnabled: Bool
    public var caseSensitive: Bool
    public var preserveCase: Bool
    public var expansionKey: ExpansionKey
    public var triggerPrefix: String

    public init(
        serviceEnabled: Bool = true,
        caseSensitive: Bool = false,
        preserveCase: Bool = true,
        expansionKey: ExpansionKey = .boundaryKey,
        triggerPrefix: String = ""
    ) {
        self.serviceEnabled = serviceEnabled
        self.caseSensitive = caseSensitive
        self.preserveCase = preserveCase
        self.expansionKey = expansionKey
        self.triggerPrefix = triggerPrefix
    }
}
