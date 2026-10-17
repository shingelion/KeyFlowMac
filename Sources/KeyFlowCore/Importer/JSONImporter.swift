import Foundation

/// Parses the app's JSON export/import schema:
///
/// ```json
/// {
///   "shortcuts": [
///     { "trigger": ";addr", "expansion": "123 Main St, Springfield", "group": "Personal", "folder": "Work", "enabled": true }
///   ]
/// }
/// ```
///
/// A bare top-level array of the same objects is also accepted. "folder" is a folder NAME (not
/// an id) resolved against existing folders at import time, creating one if it doesn't exist yet.
///
/// Every row that fails to parse is recorded as a `SkippedRow` rather than aborting the import —
/// see `ImportResolver` for the second pass that catches duplicate triggers, which needs the
/// store and so can't happen here.
public enum JSONImporter {

    public static func parse(_ json: String) -> ImportResult {
        guard let data = json.data(using: .utf8) else {
            return ImportResult(shortcuts: [], skipped: [fileLevelFailure(json, "Could not read file as UTF-8 text.")])
        }

        let array: [Any]
        do {
            let parsed = try JSONSerialization.jsonObject(with: data)
            if let bareArray = parsed as? [Any] {
                array = bareArray
            } else if let object = parsed as? [String: Any], let shortcuts = object["shortcuts"] as? [Any] {
                array = shortcuts
            } else {
                return ImportResult(shortcuts: [], skipped: [fileLevelFailure(json, "No \"shortcuts\" array found in JSON.")])
            }
        } catch {
            return ImportResult(shortcuts: [], skipped: [fileLevelFailure(json, "Invalid JSON: \(error.localizedDescription)")])
        }

        var shortcuts: [ImportedShortcut] = []
        var skipped: [SkippedRow] = []

        for (index, item) in array.enumerated() {
            let rowNumber = index + 1
            guard let obj = item as? [String: Any] else {
                skipped.append(
                    SkippedRow(
                        rowNumber: rowNumber, trigger: "", expansion: "", group: "", folder: "", enabled: "",
                        rawRow: String(describing: item), reason: "Malformed row"
                    )
                )
                continue
            }

            let rawRow = (try? JSONSerialization.data(withJSONObject: obj))
                .flatMap { String(data: $0, encoding: .utf8) } ?? String(describing: obj)
            let trigger = (obj["trigger"] as? String)?.trimmingCharacters(in: .whitespaces) ?? ""
            let expansion = obj["expansion"] as? String ?? ""
            let group = (obj["group"] as? String)?.trimmingCharacters(in: .whitespaces) ?? ""
            let folder = (obj["folder"] as? String)?.trimmingCharacters(in: .whitespaces) ?? ""
            let enabled = obj["enabled"] as? Bool ?? true

            let reason: String?
            if trigger.isEmpty { reason = "Empty trigger" }
            else if expansion.isEmpty { reason = "Empty expansion" }
            else { reason = nil }

            if let reason {
                skipped.append(
                    SkippedRow(
                        rowNumber: rowNumber, trigger: trigger, expansion: expansion,
                        group: group, folder: folder, enabled: String(enabled),
                        rawRow: rawRow, reason: reason
                    )
                )
                continue
            }

            shortcuts.append(
                ImportedShortcut(
                    shortcut: Shortcut(
                        trigger: trigger,
                        expansion: expansion,
                        isRichText: obj["isRichText"] as? Bool ?? false,
                        group: group.isEmpty ? nil : group,
                        enabled: enabled
                    ),
                    folderName: folder.isEmpty ? nil : folder,
                    rowNumber: rowNumber,
                    rawRow: rawRow
                )
            )
        }

        return ImportResult(shortcuts: shortcuts, skipped: skipped)
    }

    private static func fileLevelFailure(_ rawContent: String, _ reason: String) -> SkippedRow {
        SkippedRow(
            rowNumber: 0, trigger: "", expansion: "", group: "", folder: "", enabled: "",
            rawRow: String(rawContent.prefix(500)), reason: reason
        )
    }

    public static func toJSON(_ shortcuts: [Shortcut], folderNamesById: [UUID: String] = [:]) -> String {
        let array: [[String: Any]] = shortcuts.map { s in
            [
                "trigger": s.trigger,
                "expansion": s.expansion,
                "isRichText": s.isRichText,
                "group": s.group as Any? ?? NSNull(),
                "folder": s.folderId.flatMap { folderNamesById[$0] } as Any? ?? NSNull(),
                "enabled": s.enabled,
            ]
        }
        let root: [String: Any] = ["shortcuts": array]
        guard let data = try? JSONSerialization.data(withJSONObject: root, options: [.prettyPrinted, .sortedKeys]),
              let text = String(data: data, encoding: .utf8) else {
            return "{\"shortcuts\":[]}"
        }
        return text
    }
}
