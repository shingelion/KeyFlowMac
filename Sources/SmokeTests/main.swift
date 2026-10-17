import Foundation
import KeyFlowCore

// Dependency-free mirror of Tests/KeyFlowCoreTests, for toolchains where swift-testing's macro
// plugin isn't available (see the comment in Package.swift). Exits non-zero on any failure so it
// works as a CI gate too.

var failures = 0
var checks = 0

func check(_ name: String, file: String = #file, line: Int = #line, _ condition: @autoclosure () -> Bool) {
    checks += 1
    if !condition() {
        failures += 1
        print("FAIL: \(name) (\(file):\(line))")
    }
}

func makeEngine() -> ExpansionEngine {
    let engine = ExpansionEngine()
    engine.updateShortcuts([
        Shortcut(trigger: ";addr", expansion: "123 Main St"),
        Shortcut(trigger: ";a", expansion: "short"),
        Shortcut(trigger: "sig", expansion: "Best, Erwan"),
    ])
    return engine
}

// MARK: - ExpansionEngine + CaseAdapter

do {
    let engine = makeEngine()
    let settings = AppSettings(expansionKey: .boundaryKey)
    check("boundary: no trailing delimiter -> nil", engine.findMatch(textBeforeCursor: "Hello ;addr", settings: settings) == nil)
    let match = engine.findMatch(textBeforeCursor: "Hello ;addr ", settings: settings)
    check("boundary: matches trigger", match?.shortcut.trigger == ";addr")
    check("boundary: matchStart", match?.matchStart == 6)
    check("boundary: matchEnd", match?.matchEnd == 11)
}

do {
    let engine = makeEngine()
    let settings = AppSettings(expansionKey: .immediate)
    check("immediate: fires without delimiter", engine.findMatch(textBeforeCursor: "Hello ;addr", settings: settings)?.shortcut.trigger == ";addr")
}

do {
    let engine = makeEngine()
    let settings = AppSettings(expansionKey: .immediate)
    check("longest trigger wins", engine.findMatch(textBeforeCursor: ";addr", settings: settings)?.shortcut.trigger == ";addr")
}

do {
    let engine = makeEngine()
    let settings = AppSettings(expansionKey: .immediate)
    check("no mid-word match", engine.findMatch(textBeforeCursor: "designsig", settings: settings) == nil)
    check("boundary-preceded match ok", engine.findMatch(textBeforeCursor: "design sig", settings: settings)?.shortcut.trigger == "sig")
}

do {
    let engine = makeEngine()
    let settings = AppSettings(expansionKey: .immediate, triggerPrefix: ";")
    check("required prefix char enforced", engine.findMatch(textBeforeCursor: "besig", settings: settings) == nil)
}

do {
    let engine = makeEngine()
    check("case-insensitive by default", engine.findMatch(textBeforeCursor: ";ADDR", settings: AppSettings(caseSensitive: false, expansionKey: .immediate))?.shortcut.trigger == ";addr")
    check("case-sensitive rejects mismatch", engine.findMatch(textBeforeCursor: ";ADDR", settings: AppSettings(caseSensitive: true, expansionKey: .immediate)) == nil)
}

check("case adapter: all caps", CaseAdapter.apply(typedTrigger: ";ADDR", expansion: "hello") == "HELLO")
check("case adapter: title case", CaseAdapter.apply(typedTrigger: ";Addr", expansion: "hello there") == "Hello there")
check("case adapter: lowercase untouched", CaseAdapter.apply(typedTrigger: ";addr", expansion: "hello") == "hello")

// MARK: - PlaceholderProcessor

func fixedNow() -> Date {
    var components = DateComponents()
    components.year = 2026
    components.month = 3
    components.day = 5
    components.hour = 14
    components.minute = 30
    components.second = 0
    var calendar = Calendar(identifier: .gregorian)
    calendar.timeZone = TimeZone.current
    return calendar.date(from: components)!
}

do {
    let result = PlaceholderProcessor.process("Dear {cursor},\n\nBest", now: fixedNow())
    check("cursor placeholder text", result.text == "Dear ,\n\nBest")
    check("cursor placeholder offset", result.cursorOffset == 5)
}
do {
    let result = PlaceholderProcessor.process("plain text", now: fixedNow())
    check("no placeholder: text unchanged", result.text == "plain text")
    check("no placeholder: nil offset", result.cursorOffset == nil)
}
do {
    let result = PlaceholderProcessor.process("{date:yyyy-MM-dd} {time:HH:mm}", now: fixedNow())
    check("date/time custom patterns", result.text == "2026-03-05 14:30")
}

// MARK: - Importers

do {
    let json = """
    { "shortcuts": [
        { "trigger": ";addr", "expansion": "123 Main St", "group": "Personal" },
        { "trigger": ";sig", "expansion": "Best, Erwan", "enabled": false }
    ] }
    """
    let result = JSONImporter.parse(json)
    check("json: count", result.shortcuts.count == 2)
    check("json: trigger", result.shortcuts.first?.shortcut.trigger == ";addr")
    check("json: group", result.shortcuts.first?.shortcut.group == "Personal")
    check("json: enabled false", result.shortcuts.last?.shortcut.enabled == false)
}

do {
    let parsed = JSONImporter.parse("{ \"shortcuts\": [{ \"trigger\": \";a\", \"expansion\": \"b\" }] }")
    let exported = JSONImporter.toJSON(parsed.shortcuts.map(\.shortcut))
    let reparsed = JSONImporter.parse(exported)
    check("json round-trip", parsed.shortcuts.map { $0.shortcut.trigger } == reparsed.shortcuts.map { $0.shortcut.trigger })
}

do {
    let csv = "trigger,expansion,group\n;addr,\"123 Main St, Apt 4\",Personal\n"
    let result = CSVImporter.parse(csv)
    check("csv: count", result.shortcuts.count == 1)
    check("csv: quoted comma preserved", result.shortcuts.first?.shortcut.expansion == "123 Main St, Apt 4")
}

do {
    let csv = "trigger,expansion\n;onlytrigger,\n"
    let result = CSVImporter.parse(csv)
    check("csv: missing field skipped", result.shortcuts.isEmpty)
    check("csv: skipped count", result.skipped.count == 1)
    check("csv: skip reason", result.skipped.first?.reason == "Empty expansion")
}

do {
    let csv = "trigger,expansion\nnocommahere\n"
    let result = CSVImporter.parse(csv)
    check("csv: malformed row reason", result.skipped.first?.reason == "Malformed CSV row")
}

do {
    let existing = Shortcut(trigger: ";addr", expansion: "old")
    let result = ImportResult(shortcuts: [
        ImportedShortcut(shortcut: Shortcut(trigger: ";addr", expansion: "new"), rowNumber: 1, rawRow: ";addr,new"),
    ])
    let resolved = await ImportResolver.resolve(
        result: result,
        getExistingByTrigger: { _ in existing },
        resolveFolderId: { _ in UUID() },
        overwriteDuplicates: false
    )
    check("resolver: skips duplicate by default", resolved.toSave.isEmpty && resolved.skipped.first?.reason == "Duplicate trigger")

    let overwritten = await ImportResolver.resolve(
        result: result,
        getExistingByTrigger: { _ in existing },
        resolveFolderId: { _ in UUID() },
        overwriteDuplicates: true
    )
    check("resolver: overwrite replaces existing id", overwritten.toSave.first?.id == existing.id)
}

print("\(checks - failures)/\(checks) checks passed")
if failures > 0 {
    exit(1)
}
