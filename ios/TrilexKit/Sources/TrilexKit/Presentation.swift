// Port of the decisions in trilex/render.py: which sections a result page
// shows, in what order, with which notes. The desktop turns these into HTML;
// the iPhone app turns them into SwiftUI views. Keeping the choices here
// means they can be tested without a UI.
import Foundation

public struct WordLink: Hashable, Sendable {
    public let word: String
    public let lang: String
    public init(word: String, lang: String) { self.word = word; self.lang = lang }
}

public struct TranslationItem: Hashable, Sendable {
    public let word: String
    public let lang: String
    public let reading: String?
    public let glosses: [String]
    /// The English terms that bridged a pivot-derived pair.
    public let via: [String]
}

public struct TranslationSection: Hashable, Sendable {
    public let title: String
    public let lang: String
    public let note: String?
    public let items: [TranslationItem]
}

/// A run of text, bold or not: the headword highlighted inside a sentence.
public struct Segment: Hashable, Sendable {
    public let text: String
    public let bold: Bool
}

public struct ExampleLine: Hashable, Sendable {
    public let segments: [Segment]
    public let translation: String?
}

public struct ExampleSection: Hashable, Sendable {
    public let title: String
    public let lang: String
    public let lines: [ExampleLine]
}

public struct Header: Sendable {
    public let entry: Entry
    public let langName: String
    public let audio: AudioInfo?
    public let image: ImageInfo?
    /// Attribution line for the illustration, as the desktop truncates it.
    public let imageCredit: String?
}

public struct Page: Sendable {
    public let query: String
    public let found: Bool
    public let didYouMean: [Suggestion]

    public let resolvedFrom: String?
    public let header: Header?
    public let wiktionaryOnly: Bool
    public let alsoFormOf: (tags: String, link: WordLink)?
    public let alsoPOS: [String]

    public let definitions: [String]
    public let forms: [(label: String, link: WordLink)]
    public let translations: [TranslationSection]
    public let related: TranslationSection?
    public let examples: [ExampleSection]
    public let etymologies: [Etymology]
}

public extension Page {
    static let wiktionaryOnlyNote = "Not in Folkets or CC-CEDICT. Definition and pronunciation "
        + "from Wiktionary; any translations below are reached through English."
    static let throughEnglishNote = "Matched through English — no direct Chinese–Swedish source exists."
    static let noEtymologyNote = "No etymology recorded for this word."

    init(_ res: Result) {
        query = res.query
        found = res.found
        didYouMean = res.didYouMean
        guard let primary = res.entries.first else {
            resolvedFrom = nil; header = nil; wiktionaryOnly = false; alsoFormOf = nil; alsoPOS = []
            definitions = []; forms = []; translations = []; related = nil; examples = []
            etymologies = []
            return
        }
        resolvedFrom = res.resolvedFrom
        let credit = res.image.map { im in
            String([im.artist, im.license].compactMap { $0 }.filter { !$0.isEmpty }
                .joined(separator: " · ").prefix(60))
        }
        header = Header(entry: primary, langName: LANG_NAME[primary.lang] ?? primary.lang,
                        audio: res.audio, image: res.image,
                        imageCredit: (credit ?? "").isEmpty ? nil : credit)
        wiktionaryOnly = res.wiktionaryOnly
        alsoFormOf = res.alsoFormOf.map { ($0.tags, WordLink(word: $0.lemma, lang: $0.lang)) }

        // Homographs: 'water' is a noun and a verb, and Folkets stores them apart.
        var uniq: [String] = []
        for e in res.entries where e.word == primary.word {
            if let p = e.pos, !p.isEmpty, !uniq.contains(p) { uniq.append(p) }
        }
        alsoPOS = uniq.count > 1 ? Array(uniq.dropFirst()) : []

        // Definition: what the word means in its own language.
        var own: [String] = []
        for e in res.entries.prefix(4) {
            for sn in e.senses where sn.lang == e.lang && !own.contains(sn.gloss) {
                own.append(sn.gloss)
            }
        }
        own += (res.definitions[primary.lang] ?? []).filter { !own.contains($0) }
        definitions = Array(own.prefix(10))

        forms = (res.inflections[primary.lang] ?? []).map {
            ($0.label, WordLink(word: $0.form, lang: primary.lang))
        }

        // Translations, one section per other language.
        var sections: [TranslationSection] = []
        for lg in ["sv", "zh", "en"] where lg != primary.lang {
            var items: [TranslationItem] = []
            var seen = Set<String>()
            // Translations recorded on the entry itself are authoritative.
            for g in res.direct[lg] ?? [] where !seen.contains(g) {
                seen.insert(g)
                items.append(TranslationItem(word: g, lang: lg, reading: nil, glosses: [], via: []))
            }
            let indirect = primary.lang != "en" && lg != "en"
            for r in res.related[lg] ?? [] {
                if seen.contains(r.word) || items.count >= 14 { continue }
                seen.insert(r.word)
                items.append(TranslationItem(word: r.word, lang: lg, reading: r.reading,
                                             glosses: Array(r.glosses.prefix(3)),
                                             via: indirect ? Array(r.via.prefix(2)) : []))
            }
            if items.isEmpty { continue }
            let note = indirect && (res.direct[lg] ?? []).isEmpty ? Self.throughEnglishNote : nil
            sections.append(TranslationSection(title: LANG_NAME[lg] ?? lg, lang: lg, note: note, items: items))
        }
        translations = sections

        // Related words in the query's own language (synonyms via English).
        let same = (res.related[primary.lang] ?? []).filter { $0.score > 0.25 }.prefix(8)
        related = same.isEmpty ? nil : TranslationSection(
            title: "Related \(LANG_NAME[primary.lang] ?? primary.lang)", lang: primary.lang, note: nil,
            items: same.map { TranslationItem(word: $0.word, lang: primary.lang, reading: $0.reading,
                                              glosses: Array($0.glosses.prefix(3)), via: []) })

        var exSections: [ExampleSection] = []
        for (lg, exs) in res.examples where !exs.isEmpty {
            let title = lg == primary.lang ? "Examples" : "Examples (\(LANG_NAME[lg] ?? lg))"
            var forms = Set([primary.word]).union((res.inflections[primary.lang] ?? []).map(\.form))
            if lg != primary.lang { forms = Set((res.direct[lg] ?? []).prefix(2)) }
            exSections.append(ExampleSection(title: title, lang: lg, lines: exs.map {
                ExampleLine(segments: highlight($0.text, forms: forms), translation: $0.trans)
            }))
        }
        examples = exSections
        etymologies = res.etymologies.filter { !($0.text ?? "").isEmpty }
    }
}

/// Bold the headword (in any of its inflected forms) inside a sentence: the
/// longest form that occurs as a whole word, at most twice.
public func highlight(_ sentence: String, forms: Set<String>) -> [Segment] {
    let ordered = forms.filter { Norm.length($0) >= 2 }
        .sorted { Norm.length($0) != Norm.length($1) ? Norm.length($0) > Norm.length($1) : $0 < $1 }
    let ns = sentence as NSString
    for f in ordered {
        let pattern = "(?<!\\w)(" + NSRegularExpression.escapedPattern(for: f) + ")(?!\\w)"
        guard let re = try? NSRegularExpression(pattern: pattern, options: [.caseInsensitive]) else { continue }
        let matches = re.matches(in: sentence, range: NSRange(location: 0, length: ns.length)).prefix(2)
        if matches.isEmpty { continue }
        var out: [Segment] = []
        var at = 0
        for m in matches {
            if m.range.location > at {
                out.append(Segment(text: ns.substring(with: NSRange(location: at, length: m.range.location - at)),
                                   bold: false))
            }
            out.append(Segment(text: ns.substring(with: m.range), bold: true))
            at = m.range.location + m.range.length
        }
        if at < ns.length { out.append(Segment(text: ns.substring(from: at), bold: false)) }
        return out
    }
    return [Segment(text: sentence, bold: false)]
}
