import Foundation

/// What to do with one incoming sync-file record, given what this device already knows about
/// that `syncId`. See SYNC_PROTOCOL.md for the full algorithm; this matches the Android app's
/// `SyncMerger` decision for decision.
public enum SyncMergeAction<T: SyncRecord & Equatable>: Equatable {
    /// Apply locally (insert if new, overwrite if existing) using the record's fields and
    /// `dateModified` exactly as given — never re-stamped with "now".
    case upsert(T)
    /// Remove the live local record and remember the tombstone verbatim, preserving its original
    /// timestamp.
    case delete(T)
    /// Local state is already at least as new; do nothing.
    case ignore
}

public enum SyncMerger {
    /// - `localTimestamp`: epoch ms of the live local record's `dateModified`, or of a local
    ///   tombstone's `deletedAt`, or `nil` if this device has never seen this `syncId`.
    ///
    /// Incoming must be **strictly** newer to win — ties go to local, so a device re-reading the
    /// file it just wrote never rewrites anything.
    public static func resolve<T: SyncRecord & Equatable>(incoming: T, localTimestamp: Int64?) -> SyncMergeAction<T> {
        if let localTimestamp, incoming.effectiveTimestamp <= localTimestamp {
            return .ignore
        }
        return incoming.deletedAt != nil ? .delete(incoming) : .upsert(incoming)
    }

    public static func resolveAll<T: SyncRecord & Equatable>(
        incoming: [T],
        localTimestamp: (String) -> Int64?
    ) -> [SyncMergeAction<T>] {
        incoming.map { resolve(incoming: $0, localTimestamp: localTimestamp($0.syncId)) }
    }
}
