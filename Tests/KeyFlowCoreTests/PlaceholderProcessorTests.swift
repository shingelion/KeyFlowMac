import Testing
@testable import KeyFlowCore
import Foundation

struct PlaceholderProcessorTests {

    private func fixedNow() -> Date {
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

    @Test func resolvesCursorPlaceholderAndReportsOffset() {
        let result = PlaceholderProcessor.process("Dear {cursor},\n\nBest", now: fixedNow())
        #expect(result.text == "Dear ,\n\nBest")
        #expect(result.cursorOffset == 5)
    }

    @Test func leavesTextUntouchedWhenNoCursorPlaceholder() {
        let result = PlaceholderProcessor.process("plain text", now: fixedNow())
        #expect(result.text == "plain text")
        #expect(result.cursorOffset == nil)
    }

    @Test func resolvesDateAndTimeWithCustomPatterns() {
        let result = PlaceholderProcessor.process("{date:yyyy-MM-dd} {time:HH:mm}", now: fixedNow())
        #expect(result.text == "2026-03-05 14:30")
    }
}
