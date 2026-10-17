import Foundation

/// Result of resolving placeholders in an expansion body.
public struct ProcessedExpansion: Equatable {
    /// Final text to insert, with {cursor} already stripped out.
    public let text: String
    /// Character offset into `text` where the cursor should land after insertion, or nil to
    /// leave it at the end.
    public let cursorOffset: Int?
}

/// Resolves aText-style dynamic placeholders in an expansion body:
///  - {date} / {date:pattern}  -> current date, default "MMM d, yyyy"
///  - {time} / {time:pattern}  -> current time, default "h:mm a"
///  - {cursor}                 -> marks where the text cursor should land after insertion
///
/// Note: both braces are escaped in the patterns below (`\{` ... `\}`). Foundation's
/// NSRegularExpression is ICU-backed and, unlike some lenient regex engines, throws on a
/// dangling unescaped `}` — leaving it unescaped compiles fine on some platforms and crashes on
/// others, so both are always escaped here.
public enum PlaceholderProcessor {

    private static let datePlaceholder = try! NSRegularExpression(pattern: #"\{date(?::([^}]+))?\}"#)
    private static let timePlaceholder = try! NSRegularExpression(pattern: #"\{time(?::([^}]+))?\}"#)
    private static let cursorPlaceholder = "{cursor}"

    private static let defaultDatePattern = "MMM d, yyyy"
    private static let defaultTimePattern = "h:mm a"

    public static func process(_ raw: String, now: Date = Date()) -> ProcessedExpansion {
        var text = replace(datePlaceholder, in: raw) { pattern in
            format(pattern: pattern.isEmpty ? defaultDatePattern : pattern, date: now)
        }
        text = replace(timePlaceholder, in: text) { pattern in
            format(pattern: pattern.isEmpty ? defaultTimePattern : pattern, date: now)
        }

        var cursorOffset: Int? = nil
        if let range = text.range(of: cursorPlaceholder) {
            cursorOffset = text.distance(from: text.startIndex, to: range.lowerBound)
            text.removeSubrange(range)
        }

        return ProcessedExpansion(text: text, cursorOffset: cursorOffset)
    }

    private static func replace(_ regex: NSRegularExpression, in input: String, using transform: (String) -> String) -> String {
        let nsInput = input as NSString
        var result = ""
        var lastEnd = 0
        let matches = regex.matches(in: input, range: NSRange(location: 0, length: nsInput.length))
        for match in matches {
            result += nsInput.substring(with: NSRange(location: lastEnd, length: match.range.location - lastEnd))
            let captureRange = match.range(at: 1)
            let captured = captureRange.location != NSNotFound ? nsInput.substring(with: captureRange) : ""
            result += transform(captured)
            lastEnd = match.range.location + match.range.length
        }
        result += nsInput.substring(from: lastEnd)
        return result
    }

    private static func format(pattern: String, date: Date) -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = pattern
        formatter.locale = Locale.current
        return formatter.string(from: date)
    }
}
