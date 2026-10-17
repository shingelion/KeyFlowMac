import Testing
import Foundation
@testable import KeyFlowCore

struct SyncMergerTests {

    private func entry(syncId: String = UUID().uuidString.lowercased(), dateModified: Int64, deletedAt: Int64? = nil) -> SyncShortcutEntry {
        SyncShortcutEntry(
            syncId: syncId, trigger: ";t", expansion: "e", isRichText: false, group: nil, enabled: true,
            dateCreated: dateModified, dateModified: dateModified, deletedAt: deletedAt, folderSyncId: nil
        )
    }

    // MARK: merge decisions (mirror Android's SyncMerger: strictly newer wins, local wins ties)

    @Test func upsertsWhenNeverSeenLocally() {
        let incoming = entry(dateModified: 100)
        #expect(SyncMerger.resolve(incoming: incoming, localTimestamp: nil) == .upsert(incoming))
    }

    @Test func recordsTombstoneWhenNeverSeenLocally() {
        let incoming = entry(dateModified: 200, deletedAt: 200)
        #expect(SyncMerger.resolve(incoming: incoming, localTimestamp: nil) == .delete(incoming))
    }

    @Test func ignoresWhenLocalIsNewer() {
        let incoming = entry(dateModified: 100)
        #expect(SyncMerger.resolve(incoming: incoming, localTimestamp: 200) == .ignore)
    }

    @Test func upsertsWhenIncomingIsNewer() {
        let incoming = entry(dateModified: 200)
        #expect(SyncMerger.resolve(incoming: incoming, localTimestamp: 100) == .upsert(incoming))
    }

    @Test func tiesGoToLocal() {
        let incoming = entry(dateModified: 100)
        #expect(SyncMerger.resolve(incoming: incoming, localTimestamp: 100) == .ignore)
    }

    @Test func newerTombstoneDeletesOlderLiveRecord() {
        let incoming = entry(dateModified: 200, deletedAt: 200)
        #expect(SyncMerger.resolve(incoming: incoming, localTimestamp: 100) == .delete(incoming))
    }

    @Test func olderTombstoneLosesToNewerLiveRecord() {
        let incoming = entry(dateModified: 100, deletedAt: 100)
        #expect(SyncMerger.resolve(incoming: incoming, localTimestamp: 200) == .ignore)
    }

    @Test func newerLiveRecordResurrectsOlderLocalTombstone() {
        let incoming = entry(dateModified: 200)
        #expect(SyncMerger.resolve(incoming: incoming, localTimestamp: 100) == .upsert(incoming))
    }

    @Test func staleDateModifiedDoesNotHideANewerDelete() {
        // A tombstone whose dateModified lags its deletedAt still counts as a delete at deletedAt.
        let incoming = entry(dateModified: 50, deletedAt: 300)
        #expect(SyncMerger.resolve(incoming: incoming, localTimestamp: 100) == .delete(incoming))
    }

    @Test func resolveAllLooksUpLocalTimestampPerSyncId() {
        let a = entry(syncId: "a", dateModified: 200)
        let b = entry(syncId: "b", dateModified: 100)
        let actions = SyncMerger.resolveAll(incoming: [a, b]) { ["b": 300][$0] }
        #expect(actions == [.upsert(a), .ignore])
    }

    // MARK: id mapping

    @Test func parsesLowercaseDashedUUIDAsCanonical() {
        let uuid = UUID()
        let wire = uuid.uuidString.lowercased()
        let parsed = SyncIdMapper.parse(wire)
        #expect(parsed?.uuid == uuid)
        #expect(parsed?.canonical == wire)
    }

    @Test func parsesAndroidMigrated32HexId() {
        let hex = "0123456789abcdef0123456789abcdef"
        let parsed = SyncIdMapper.parse(hex)
        #expect(parsed != nil)
        #expect(parsed?.canonical == "01234567-89ab-cdef-0123-456789abcdef")
        // Differs from the wire form, so the caller must remember `hex` to write it back verbatim.
        #expect(parsed?.canonical != hex)
    }

    @Test func rejectsUnrecognizedIds() {
        #expect(SyncIdMapper.parse("not-an-id") == nil)
        #expect(SyncIdMapper.parse("0123456789abcdef") == nil)
        #expect(SyncIdMapper.parse("zzzz456789abcdef0123456789abcdef") == nil)
    }

    // MARK: wire format

    /// Shaped like a real file from the Android app: version, explicit nulls, epoch-ms Longs,
    /// a 32-hex migrated syncId, and a tombstone.
    private let androidSample = """
    {
      "version": 1,
      "folders": [
        {"syncId": "8b2c1a00-1111-4222-8333-444455556666", "name": "Work", "colorTag": null,
         "sortOrder": 0, "dateCreated": 1700000000000, "dateModified": 1700000005000, "deletedAt": null}
      ],
      "shortcuts": [
        {"syncId": "0123456789abcdef0123456789abcdef", "trigger": ";addr", "expansion": "123 Main St",
         "isRichText": false, "group": null, "enabled": true, "dateCreated": 1700000000000,
         "dateModified": 1700000123456, "deletedAt": null, "folderSyncId": "8b2c1a00-1111-4222-8333-444455556666"},
        {"syncId": "5a4543dc-1709-4a4a-9cbc-ea7d7bc02ba2", "trigger": ";gone", "expansion": "x",
         "isRichText": false, "group": "old", "enabled": false, "dateCreated": 1700000000000,
         "dateModified": 1700000200000, "deletedAt": 1700000200000, "folderSyncId": null}
      ]
    }
    """

    @Test func decodesAnAndroidWrittenFile() throws {
        let file = try SyncFile.decode(from: Data(androidSample.utf8))
        #expect(file.version == 1)
        #expect(file.folders.count == 1)
        #expect(file.shortcuts.count == 2)
        #expect(file.shortcuts[0].syncId == "0123456789abcdef0123456789abcdef")
        #expect(file.shortcuts[0].dateModified == 1_700_000_123_456)
        #expect(file.shortcuts[0].folderSyncId == "8b2c1a00-1111-4222-8333-444455556666")
        #expect(file.shortcuts[1].deletedAt == 1_700_000_200_000)
        #expect(file.shortcuts[1].group == "old")
    }

    @Test func roundTripsWithoutChangingAnyValue() throws {
        let original = try SyncFile.decode(from: Data(androidSample.utf8))
        let again = try SyncFile.decode(from: original.encoded())
        #expect(again.isEquivalent(to: original))
    }

    @Test func encodesVersionAndExplicitNulls() throws {
        let file = SyncFile(folders: [], shortcuts: [entry(syncId: "a", dateModified: 1)])
        let json = String(decoding: try file.encoded(), as: UTF8.self)
        #expect(json.contains("\"version\" : 1"))
        #expect(json.contains("\"deletedAt\" : null"))
        #expect(json.contains("\"folderSyncId\" : null"))
        #expect(json.contains("\"dateModified\" : 1"))  // an integer, not a float or a string
    }

    @Test func skipsUnreadableEntriesInsteadOfFailingTheWholeFile() throws {
        let json = """
        {"shortcuts": [
          {"trigger": "no syncId"},
          {"syncId": "  ", "trigger": "blank"},
          {"syncId": "ok", "trigger": ";ok", "expansion": "fine", "dateModified": 5}
        ]}
        """
        let file = try SyncFile.decode(from: Data(json.utf8))
        #expect(file.shortcuts.map(\.syncId) == ["ok"])
        #expect(file.folders.isEmpty)
    }

    @Test func missingOptionalFieldsFallBackToAndroidsDefaults() throws {
        let json = #"{"shortcuts": [{"syncId": "x", "trigger": ";x"}]}"#
        let s = try SyncFile.decode(from: Data(json.utf8)).shortcuts[0]
        #expect(s.enabled == true)
        #expect(s.isRichText == false)
        #expect(s.dateModified == 0)
        #expect(s.deletedAt == nil)
        #expect(s.group == nil)
    }

    @Test func equivalenceIgnoresOrderAndVersion() {
        let a = entry(syncId: "a", dateModified: 1)
        let b = entry(syncId: "b", dateModified: 2)
        let f1 = SyncFile(version: 1, folders: [], shortcuts: [a, b])
        let f2 = SyncFile(version: 2, folders: [], shortcuts: [b, a])
        #expect(f1.isEquivalent(to: f2))
        #expect(!f1.isEquivalent(to: SyncFile(folders: [], shortcuts: [a])))
    }

    // MARK: local <-> wire conversion

    @Test func tombstoneBumpsDateModifiedToDeletedAt() {
        let shortcut = Shortcut(trigger: ";t", expansion: "e", dateCreated: SyncTime.date(ms: 1000), dateModified: SyncTime.date(ms: 2000))
        let tomb = SyncShortcutEntry(shortcut, syncId: "id", folderSyncId: nil, deletedAt: 9000)
        #expect(tomb.deletedAt == 9000)
        #expect(tomb.dateModified == 9000)  // not 2000 — the receiver only accepts a delete newer than its copy
        let live = SyncShortcutEntry(shortcut, syncId: "id", folderSyncId: nil)
        #expect(live.dateModified == 2000)
        #expect(live.deletedAt == nil)
    }

    @Test func timestampsSurviveTheMillisecondRoundTrip() {
        let ms: Int64 = 1_700_000_123_456
        #expect(SyncTime.ms(SyncTime.date(ms: ms)) == ms)
    }

    // MARK: first-contact identity adoption

    @Test func adoptsTheFilesIdentityForALocalRecordWithTheSameTrigger() {
        let local = UUID()
        let pairs = SyncIdentityAdopter.pair(
            incoming: [("hexid1", ";Addr")],
            locals: [(local, " ;addr ")]
        )
        #expect(pairs == [.init(syncId: "hexid1", localId: local)])
    }

    @Test func eachLocalRecordIsClaimedAtMostOnce() {
        let only = UUID()
        let pairs = SyncIdentityAdopter.pair(
            incoming: [("a", ";x"), ("b", ";x")],
            locals: [(only, ";x")]
        )
        #expect(pairs == [.init(syncId: "a", localId: only)])  // "b" stays unmatched and is inserted as new
    }

    @Test func duplicateLocalTriggersPairOffInOrder() {
        let l1 = UUID(), l2 = UUID()
        let pairs = SyncIdentityAdopter.pair(
            incoming: [("a", ";x"), ("b", ";x")],
            locals: [(l1, ";x"), (l2, ";x")]
        )
        #expect(pairs == [.init(syncId: "a", localId: l1), .init(syncId: "b", localId: l2)])
    }

    @Test func noMatchMeansNoAdoptionAndBlankKeysNeverMatch() {
        #expect(SyncIdentityAdopter.pair(incoming: [("a", ";one")], locals: [(UUID(), ";two")]).isEmpty)
        #expect(SyncIdentityAdopter.pair(incoming: [("a", "  ")], locals: [(UUID(), "")]).isEmpty)
    }
}
