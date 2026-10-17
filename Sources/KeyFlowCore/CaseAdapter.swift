import Foundation

/// Mirrors aText's auto-capitalize behavior: if the user typed the trigger in all caps or
/// capitalized the first letter, the expansion is adjusted to match.
public enum CaseAdapter {

    public static func apply(typedTrigger: String, expansion: String) -> String {
        let letters = typedTrigger.filter { $0.isLetter }
        guard let firstLetter = letters.first else { return expansion }

        if letters == letters.uppercased() && letters != letters.lowercased() {
            return expansion.uppercased()
        }
        if firstLetter.isUppercase {
            guard let first = expansion.first else { return expansion }
            return first.uppercased() + expansion.dropFirst()
        }
        return expansion
    }
}
