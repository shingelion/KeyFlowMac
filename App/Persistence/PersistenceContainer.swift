import Foundation
import SwiftData

/// Single shared SwiftData container for both entities — mirrors the Android app's one Room
/// `AppDatabase` with two DAOs. `ShortcutRecord.folderId` is a plain `UUID?`, not a SwiftData
/// `@Relationship`, so both stores just need to point at the same container/context to stay
/// consistent; there's no real foreign key for SwiftData to enforce.
@MainActor
enum PersistenceContainer {
    static let shared: ModelContainer = {
        let schema = Schema([ShortcutRecord.self, FolderRecord.self])
        let config = ModelConfiguration(schema: schema, isStoredInMemoryOnly: false)
        do {
            return try ModelContainer(for: schema, configurations: [config])
        } catch {
            fatalError("Could not create ModelContainer: \(error)")
        }
    }()

    static func newContext() -> ModelContext {
        ModelContext(shared)
    }
}
