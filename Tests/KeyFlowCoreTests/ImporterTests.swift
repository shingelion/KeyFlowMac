import Testing
import Foundation
@testable import KeyFlowCore

struct ImporterTests {

    @Test func jsonImporterParsesShortcutsArraySchema() {
        let json = """
        { "shortcuts": [
            { "trigger": ";addr", "expansion": "123 Main St", "group": "Personal" },
            { "trigger": ";sig", "expansion": "Best, Erwan", "enabled": false }
        ] }
        """
        let result = JSONImporter.parse(json)
        #expect(result.shortcuts.count == 2)
        #expect(result.shortcuts[0].shortcut.trigger == ";addr")
        #expect(result.shortcuts[0].shortcut.group == "Personal")
        #expect(result.shortcuts[1].shortcut.enabled == false)
    }

    @Test func jsonImporterRoundTripsItsOwnExport() {
        let parsed = JSONImporter.parse("{ \"shortcuts\": [{ \"trigger\": \";a\", \"expansion\": \"b\" }] }")
        let exported = JSONImporter.toJSON(parsed.shortcuts.map(\.shortcut))
        let reparsed = JSONImporter.parse(exported)
        #expect(parsed.shortcuts.map { $0.shortcut.trigger } == reparsed.shortcuts.map { $0.shortcut.trigger })
    }

    @Test func jsonImporterFlagsEmptyFieldsWithReasons() {
        let json = """
        { "shortcuts": [
            { "trigger": "", "expansion": "b" },
            { "trigger": ";a", "expansion": "" }
        ] }
        """
        let result = JSONImporter.parse(json)
        #expect(result.shortcuts.isEmpty)
        #expect(result.skipped.map(\.reason) == ["Empty trigger", "Empty expansion"])
    }

    @Test func csvImporterSkipsHeaderRowAndHandlesQuotedCommas() {
        let csv = "trigger,expansion,group\n;addr,\"123 Main St, Apt 4\",Personal\n"
        let result = CSVImporter.parse(csv)
        #expect(result.shortcuts.count == 1)
        #expect(result.shortcuts[0].shortcut.expansion == "123 Main St, Apt 4")
    }

    @Test func csvImporterFlagsRowsMissingRequiredFields() {
        let csv = "trigger,expansion\n;onlytrigger,\n"
        let result = CSVImporter.parse(csv)
        #expect(result.shortcuts.isEmpty)
        #expect(result.skipped.count == 1)
        #expect(result.skipped[0].reason == "Empty expansion")
    }

    @Test func csvImporterFlagsMalformedRow() {
        let csv = "trigger,expansion\nnocommahere\n"
        let result = CSVImporter.parse(csv)
        #expect(result.skipped.first?.reason == "Malformed CSV row")
    }

    @Test func csvImporterParsesFolderColumnAndRoundTripsSkippedExport() {
        let csv = "trigger,expansion,group,enabled,folder\n;a,b,,true,Work\n"
        let result = CSVImporter.parse(csv)
        #expect(result.shortcuts.first?.folderName == "Work")

        let skippedCsv = CSVImporter.skippedToCSV([
            SkippedRow(rowNumber: 2, trigger: ";x", expansion: "", group: "", folder: "", enabled: "", rawRow: ";x,", reason: "Empty expansion"),
        ])
        #expect(skippedCsv.contains("reason"))
        #expect(skippedCsv.contains("Empty expansion"))
    }

    @Test func resolverSkipsDuplicateWithinSameBatch() async {
        let result = ImportResult(shortcuts: [
            ImportedShortcut(shortcut: Shortcut(trigger: ";dup", expansion: "one"), rowNumber: 1, rawRow: ";dup,one"),
            ImportedShortcut(shortcut: Shortcut(trigger: ";dup", expansion: "two"), rowNumber: 2, rawRow: ";dup,two"),
        ])
        let resolved = await ImportResolver.resolve(
            result: result,
            getExistingByTrigger: { _ in nil },
            resolveFolderId: { _ in UUID() },
            overwriteDuplicates: false
        )
        #expect(resolved.toSave.count == 1)
        #expect(resolved.skipped.count == 1)
        #expect(resolved.skipped.first?.reason == "Duplicate trigger")
    }

    @Test func resolverSkipsExistingDuplicateUnlessOverwriteEnabled() async {
        let existing = Shortcut(trigger: ";addr", expansion: "old")
        let result = ImportResult(shortcuts: [
            ImportedShortcut(shortcut: Shortcut(trigger: ";addr", expansion: "new"), rowNumber: 1, rawRow: ";addr,new"),
        ])

        let skipped = await ImportResolver.resolve(
            result: result,
            getExistingByTrigger: { _ in existing },
            resolveFolderId: { _ in UUID() },
            overwriteDuplicates: false
        )
        #expect(skipped.toSave.isEmpty)
        #expect(skipped.skipped.first?.reason == "Duplicate trigger")

        let overwritten = await ImportResolver.resolve(
            result: result,
            getExistingByTrigger: { _ in existing },
            resolveFolderId: { _ in UUID() },
            overwriteDuplicates: true
        )
        #expect(overwritten.toSave.count == 1)
        #expect(overwritten.toSave.first?.id == existing.id)
        #expect(overwritten.toSave.first?.expansion == "new")
    }
}
