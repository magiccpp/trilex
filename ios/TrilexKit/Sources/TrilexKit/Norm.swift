// Port of trilex/norm.py. The lookup keys in dict.db were written by the
// Python builder, so these must produce byte-identical strings or a search
// silently finds nothing.
//
// Swedish a-ring/a-umlaut/o-umlaut are *letters*, not accented vowels, so
// `norm` never folds them away. `loose` does, and is only ever used to rescue
// a typo - never to decide an exact match.
import Foundation

public enum Norm {
    /// Python's `str.casefold` differs from `lower` for a handful of
    /// characters; these are the ones that can occur in a Latin-script query.
    private static let fold: [Unicode.Scalar: String] = [
        "\u{00DF}": "ss", "\u{1E9E}": "ss",          // ß ẞ
        "\u{03C2}": "\u{03C3}",                      // final sigma
        "\u{017F}": "s",                             // long s
        "\u{FB00}": "ff", "\u{FB01}": "fi", "\u{FB02}": "fl",
        "\u{FB03}": "ffi", "\u{FB04}": "ffl", "\u{FB05}": "st", "\u{FB06}": "st",
        "\u{0149}": "\u{02BC}n",
    ]

    static func casefold(_ s: String) -> String {
        var out = ""
        out.reserveCapacity(s.utf8.count)
        for u in s.unicodeScalars {
            if u.isASCII {
                out.unicodeScalars.append(
                    (u.value >= 65 && u.value <= 90) ? Unicode.Scalar(u.value + 32)! : u)
            } else if let f = fold[u] {
                out += f
            } else {
                out += u.properties.lowercaseMapping
            }
        }
        return out
    }

    /// What `re`'s `\s` matches in a str pattern (the str.isspace set).
    static func isSpace(_ v: UInt32) -> Bool {
        switch v {
        case 0x09...0x0D, 0x1C...0x20, 0x85, 0xA0, 0x1680, 0x2000...0x200A,
             0x2028, 0x2029, 0x202F, 0x205F, 0x3000:
            return true
        default:
            return false
        }
    }

    /// The `_PUNCT` character class.
    static func isPunct(_ v: UInt32) -> Bool {
        if isSpace(v) { return true }
        switch v {
        case 0x21...0x2F, 0x3A...0x40, 0x5B...0x60, 0x7B...0x7E,
             0x2010...0x2027, 0x3001...0x303F, 0xFF01...0xFF65:
            return true
        default:
            return false
        }
    }

    /// Lookup key: NFC, casefolded, punctuation and spacing removed.
    public static func norm(_ s: String) -> String {
        let folded = casefold(s.precomposedStringWithCanonicalMapping)
        var out = String.UnicodeScalarView()
        var pendingSpace = false
        for u in folded.unicodeScalars {
            if isPunct(u.value) {
                pendingSpace = true
                continue
            }
            if pendingSpace && !out.isEmpty { out.append(" ") }
            pendingSpace = false
            out.append(u)
        }
        return String(out)
    }

    /// Typo-tolerant key: additionally strips diacritics and inner spaces.
    public static func loose(_ s: String) -> String {
        let n = norm(s)
        var out = String.UnicodeScalarView()
        if n.utf8.allSatisfy({ $0 < 0x80 }) {
            // Fast path: nothing to decompose. Most of the fuzzy pool is ASCII.
            for u in n.unicodeScalars where u != " " { out.append(u) }
            return String(out)
        }
        for u in n.decomposedStringWithCanonicalMapping.unicodeScalars {
            if u == " " || u.properties.canonicalCombiningClass != .notReordered { continue }
            out.append(u)
        }
        return String(out)
    }

    public static func isCJK(_ u: Unicode.Scalar) -> Bool {
        switch u.value {
        case 0x3400...0x4DBF, 0x4E00...0x9FFF, 0xF900...0xFAFF: return true
        default: return false
        }
    }

    public static func isCJK(_ s: String) -> Bool {
        s.unicodeScalars.contains(where: isCJK)
    }

    /// Python's `str.isupper`: at least one cased character, none lowercase.
    static func isUpper(_ s: String) -> Bool {
        var cased = false
        for u in s.unicodeScalars {
            let p = u.properties
            if p.isLowercase || p.generalCategory == .titlecaseLetter { return false }
            if p.isUppercase { cased = true }
        }
        return cased
    }

    /// Python's `len`: code points, not grapheme clusters.
    static func length(_ s: String) -> Int { s.unicodeScalars.count }
}
