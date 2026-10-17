import Foundation

/// Imports shortcuts from CSV with columns `trigger,expansion` (optional `group`, `enabled`,
/// `folder` columns after them, in that order). A header row is detected and skipped
/// automatically if its first cell is "trigger" (case-insensitive). `folder` is a folder NAME
/// (not an id) resolved against existing folders at import time, creating one if it doesn't
/// exist yet.
///
/// Every row that fails to parse is recorded as a `SkippedRow` rather than aborting the import —
/// see `ImportResolver` for the second pass that catches duplicate triggers, which needs the
/// store and so can't happen here.
public enum CSVImporter {

    public static func parse(_ content: String) -> ImportResult {
        let rows = CSVParser.parse(content)
        guard !rows.isEmpty else {
            return ImportResult(shortcuts: [], skipped: [emptySkip(reason: "File is empty.")])
        }

        var startIndex = 0
        if let first = rows.first?.first, first.trimmingCharacters(in: .whitespaces).lowercased() == "trigger" {
            startIndex = 1
        }

        var shortcuts: [ImportedShortcut] = []
        var skipped: [SkippedRow] = []

        for i in startIndex..<rows.count {
            let row = rows[i]
            let rowNumber = i + 1
            let rawRow = row.map(CSVParser.toCSVField).joined(separator: ",")

            guard row.count >= 2 else {
                skipped.append(
                    SkippedRow(
                        rowNumber: rowNumber,
                        trigger: row.first?.trimmingCharacters(in: .whitespaces) ?? "",
                        expansion: "", group: "", folder: "", enabled: "",
                        rawRow: rawRow, reason: "Malformed CSV row"
                    )
                )
                continue
            }

            let trigger = row[0].trimmingCharacters(in: .whitespaces)
            let expansion = row[1]
            let group = row.count > 2 ? row[2].trimmingCharacters(in: .whitespaces) : ""
            let enabledRaw = row.count > 3 ? row[3].trimmingCharacters(in: .whitespaces) : ""
            let folder = row.count > 4 ? row[4].trimmingCharacters(in: .whitespaces) : ""

            let reason: String?
            if trigger.isEmpty { reason = "Empty trigger" }
            else if expansion.isEmpty { reason = "Empty expansion" }
            else { reason = nil }

            if let reason {
                skipped.append(
                    SkippedRow(
                        rowNumber: rowNumber, trigger: trigger, expansion: expansion,
                        group: group, folder: folder, enabled: enabledRaw,
                        rawRow: rawRow, reason: reason
                    )
                )
                continue
            }

            let enabled = enabledRaw.isEmpty ? true : (enabledRaw.lowercased() == "true" || enabledRaw == "1")
            shortcuts.append(
                ImportedShortcut(
                    shortcut: Shortcut(trigger: trigger, expansion: expansion, group: group.isEmpty ? nil : group, enabled: enabled),
                    folderName: folder.isEmpty ? nil : folder,
                    rowNumber: rowNumber,
                    rawRow: rawRow
                )
            )
        }

        return ImportResult(shortcuts: shortcuts, skipped: skipped)
    }

    public static func toCSV(_ shortcuts: [Shortcut], folderNamesById: [UUID: String] = [:]) -> String {
        var lines = ["trigger,expansion,group,enabled,folder"]
        for s in shortcuts {
            let fields = [
                CSVParser.toCSVField(s.trigger),
                CSVParser.toCSVField(s.expansion),
                CSVParser.toCSVField(s.group ?? ""),
                s.enabled ? "true" : "false",
                CSVParser.toCSVField(s.folderId.flatMap { folderNamesById[$0] } ?? ""),
            ]
            lines.append(fields.joined(separator: ","))
        }
        return lines.joined(separator: "\n") + "\n"
    }

    /// Writes skipped rows back out in the same columns the importer expects, plus a `reason`
    /// column, so the user can review, fix, and re-import them.
    public static func skippedToCSV(_ skipped: [SkippedRow]) -> String {
        var lines = ["trigger,expansion,group,enabled,folder,reason"]
        for row in skipped {
            let fields = [
                CSVParser.toCSVField(row.trigger),
                CSVParser.toCSVField(row.expansion),
                CSVParser.toCSVField(row.group),
                CSVParser.toCSVField(row.enabled),
                CSVParser.toCSVField(row.folder),
                CSVParser.toCSVField(row.reason),
            ]
            lines.append(fields.joined(separator: ","))
        }
        return lines.joined(separator: "\n") + "\n"
    }

    private static func emptySkip(reason: String) -> SkippedRow {
        SkippedRow(rowNumber: 0, trigger: "", expansion: "", group: "", folder: "", enabled: "", rawRow: "", reason: reason)
    }
}
