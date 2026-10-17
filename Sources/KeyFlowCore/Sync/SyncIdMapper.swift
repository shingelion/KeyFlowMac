import Foundation

/// Bridges the wire `syncId` (an opaque string) and this app's local `UUID` ids.
///
/// Android generates two shapes of syncId: `UUID.randomUUID().toString()` (lowercase, dashed) for
/// rows created after sync existed, and `lower(hex(randomblob(16)))` (32 hex chars, no dashes)
/// for rows migrated from before. The Mac side keys everything by `UUID`, so both shapes map to a
/// UUID — but the *original string* must go back out unchanged, or Android would see a brand new
/// syncId and duplicate the record. `parse` reports the canonical form so callers can remember
/// any original that differs from it.
public enum SyncIdMapper {
    public struct Parsed: Equatable {
        public let uuid: UUID
        /// What `canonicalSyncId(for: uuid)` would emit — differs from the input for 32-hex or
        /// uppercase ids, which the caller must then remember to write back verbatim.
        public let canonical: String
    }

    public static func canonicalSyncId(for uuid: UUID) -> String {
        uuid.uuidString.lowercased()
    }

    /// Returns `nil` for anything that isn't a UUID or a 32-char hex string; such records can't be
    /// keyed locally and are skipped (Android never produces them).
    public static func parse(_ syncId: String) -> Parsed? {
        if let uuid = UUID(uuidString: syncId) {
            return Parsed(uuid: uuid, canonical: canonicalSyncId(for: uuid))
        }
        guard syncId.count == 32, syncId.allSatisfy(\.isHexDigit) else { return nil }
        let chars = Array(syncId)
        let dashed = [chars[0..<8], chars[8..<12], chars[12..<16], chars[16..<20], chars[20..<32]]
            .map { String($0) }
            .joined(separator: "-")
        guard let uuid = UUID(uuidString: dashed) else { return nil }
        return Parsed(uuid: uuid, canonical: canonicalSyncId(for: uuid))
    }
}
