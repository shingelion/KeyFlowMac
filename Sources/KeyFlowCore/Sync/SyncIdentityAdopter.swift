import Foundation

/// First-contact reconciliation. Two devices that each built up the same shortcuts independently
/// (e.g. one imported its list from the other's export) hold the same logical records under
/// unrelated ids. Merging by id alone would insert every one of them a second time, on both
/// sides. So a record arriving from the file that this device has never seen — and whose
/// trigger/name matches a local record the file doesn't know about — is treated as *that* record:
/// the local one adopts the file's `syncId` instead of a duplicate being created.
public enum SyncIdentityAdopter {
    public struct Pair: Equatable {
        public let syncId: String
        public let localId: UUID
    }

    static func normalize(_ key: String) -> String {
        key.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    }

    /// - `incoming`: live records from the file with no local counterpart, as (syncId, match key).
    /// - `locals`: live local records whose own id does not appear in the file, as (id, match key).
    ///
    /// Each local record is claimed at most once, and each incoming record at most once, first
    /// come first served. Blank keys never match.
    public static func pair(
        incoming: [(syncId: String, key: String)],
        locals: [(id: UUID, key: String)]
    ) -> [Pair] {
        var unclaimed: [String: [UUID]] = [:]
        for local in locals {
            let key = normalize(local.key)
            guard !key.isEmpty else { continue }
            unclaimed[key, default: []].append(local.id)
        }

        var pairs: [Pair] = []
        for entry in incoming {
            let key = normalize(entry.key)
            guard !key.isEmpty, var candidates = unclaimed[key], !candidates.isEmpty else { continue }
            let localId = candidates.removeFirst()
            unclaimed[key] = candidates
            pairs.append(Pair(syncId: entry.syncId, localId: localId))
        }
        return pairs
    }
}
