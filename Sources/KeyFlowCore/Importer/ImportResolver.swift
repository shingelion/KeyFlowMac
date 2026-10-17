import Foundation

/// Shortcuts ready to save, plus every skipped row — structural ones from parsing and any new
/// duplicates this pass found.
public struct ResolvedImport {
    public let toSave: [Shortcut]
    public let skipped: [SkippedRow]
}

/// Second pass over a parsed `ImportResult`: resolves folder names to ids and catches duplicate
/// triggers, both within the file itself and against what's already saved. Neither check can
/// live in the parsers themselves since both need the store — so it's injected here as async
/// closures, keeping this type (and its duplicate/overwrite logic) unit-testable without a real
/// persistence layer.
public enum ImportResolver {

    public static func resolve(
        result: ImportResult,
        getExistingByTrigger: (String) async -> Shortcut?,
        resolveFolderId: (String) async -> UUID,
        overwriteDuplicates: Bool
    ) async -> ResolvedImport {
        var seenTriggers = Set<String>()
        var toSave: [Shortcut] = []
        var newSkips: [SkippedRow] = []

        for item in result.shortcuts {
            let trigger = item.shortcut.trigger
            let duplicateInBatch = !seenTriggers.insert(trigger).inserted
            let existing = duplicateInBatch ? nil : await getExistingByTrigger(trigger)

            if duplicateInBatch || (existing != nil && !overwriteDuplicates) {
                newSkips.append(item.toSkippedRow(reason: "Duplicate trigger"))
                continue
            }

            let folderId = item.folderName != nil ? await resolveFolderId(item.folderName!) : nil
            if let existing {
                toSave.append({ var s = item.shortcut; s.id = existing.id; s.folderId = folderId; return s }())
            } else {
                toSave.append({ var s = item.shortcut; s.folderId = folderId; return s }())
            }
        }

        return ResolvedImport(toSave: toSave, skipped: result.skipped + newSkips)
    }
}

private extension ImportedShortcut {
    func toSkippedRow(reason: String) -> SkippedRow {
        SkippedRow(
            rowNumber: rowNumber,
            trigger: shortcut.trigger,
            expansion: shortcut.expansion,
            group: shortcut.group ?? "",
            folder: folderName ?? "",
            enabled: String(shortcut.enabled),
            rawRow: rawRow,
            reason: reason
        )
    }
}
