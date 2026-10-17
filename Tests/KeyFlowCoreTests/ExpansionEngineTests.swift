import Testing
@testable import KeyFlowCore

struct ExpansionEngineTests {

    func makeEngine() -> ExpansionEngine {
        let engine = ExpansionEngine()
        engine.updateShortcuts([
            Shortcut(trigger: ";addr", expansion: "123 Main St"),
            Shortcut(trigger: ";a", expansion: "short"),
            Shortcut(trigger: "sig", expansion: "Best, Erwan"),
        ])
        return engine
    }

    @Test func boundaryModeRequiresTrailingDelimiter() {
        let engine = makeEngine()
        let settings = AppSettings(expansionKey: .boundaryKey)
        #expect(engine.findMatch(textBeforeCursor: "Hello ;addr", settings: settings) == nil)

        let match = engine.findMatch(textBeforeCursor: "Hello ;addr ", settings: settings)
        #expect(match?.shortcut.trigger == ";addr")
        #expect(match?.matchStart == 6)
        #expect(match?.matchEnd == 11)
    }

    @Test func immediateModeFiresWithoutDelimiter() {
        let engine = makeEngine()
        let settings = AppSettings(expansionKey: .immediate)
        let match = engine.findMatch(textBeforeCursor: "Hello ;addr", settings: settings)
        #expect(match?.shortcut.trigger == ";addr")
    }

    @Test func longestTriggerWinsOverShorterPrefix() {
        let engine = makeEngine()
        let settings = AppSettings(expansionKey: .immediate)
        let match = engine.findMatch(textBeforeCursor: ";addr", settings: settings)
        #expect(match?.shortcut.trigger == ";addr")
    }

    @Test func doesNotMatchMidWordWithoutBoundary() {
        let engine = makeEngine()
        let settings = AppSettings(expansionKey: .immediate)
        // "sig" is a suffix of "designsig", but it's glued onto a letter, not a real word boundary.
        #expect(engine.findMatch(textBeforeCursor: "designsig", settings: settings) == nil)
        // Preceded by a space, the same suffix should match fine.
        #expect(engine.findMatch(textBeforeCursor: "design sig", settings: settings)?.shortcut.trigger == "sig")
    }

    @Test func respectsConfiguredRequiredPrefixCharacter() {
        let engine = makeEngine()
        let settings = AppSettings(expansionKey: .immediate, triggerPrefix: ";")
        #expect(engine.findMatch(textBeforeCursor: "besig", settings: settings) == nil)
    }

    @Test func caseInsensitiveByDefault() {
        let engine = makeEngine()
        let settings = AppSettings(caseSensitive: false, expansionKey: .immediate)
        #expect(engine.findMatch(textBeforeCursor: ";ADDR", settings: settings)?.shortcut.trigger == ";addr")
    }

    @Test func caseSensitiveModeRejectsMismatchedCase() {
        let engine = makeEngine()
        let settings = AppSettings(caseSensitive: true, expansionKey: .immediate)
        #expect(engine.findMatch(textBeforeCursor: ";ADDR", settings: settings) == nil)
    }

    @Test func caseAdapterUppercasesWhenTriggerTypedInCaps() {
        #expect(CaseAdapter.apply(typedTrigger: ";ADDR", expansion: "hello") == "HELLO")
    }

    @Test func caseAdapterCapitalizesFirstLetterForTitleCaseTrigger() {
        #expect(CaseAdapter.apply(typedTrigger: ";Addr", expansion: "hello there") == "Hello there")
    }

    @Test func caseAdapterLeavesLowercaseTriggerUntouched() {
        #expect(CaseAdapter.apply(typedTrigger: ";addr", expansion: "hello") == "hello")
    }
}
