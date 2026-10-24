// Port of trilex/search.py: the query engine over dict.db. Pure SQLite - no
// network, no services. The Python class is `Dictionary`; here it is
// `Lexicon`, so it cannot be confused with Swift.Dictionary.
//
// Ranking rules and their comments follow the Python original; when one
// changes, change the other, or desktop and phone will disagree about what a
// word means.
import Foundation

public let LANGS = ["en", "sv", "zh"]
public let LANG_NAME = ["en": "English", "sv": "Svenska", "zh": "中文"]

public struct Sense: Equatable, Sendable {
    public let lang: String
    public let gloss: String
}

public struct Entry: Equatable, Sendable, Identifiable {
    public let id: Int
    public let lang: String
    public let word: String
    public let pos: String?
    public let extra: [String: JSONValue]
    public var senses: [Sense] = []

    public var pinyin: String? { extra["pinyin"]?.nonEmptyString }

    public var traditional: String? {
        guard let t = extra["trad"]?.nonEmptyString, t != word else { return nil }
        return t
    }

    /// The pronunciation line: pinyin for Chinese, Folkets phonetics else.
    public var reading: String? { pinyin ?? extra["phonetic"]?.nonEmptyString }

    public func glosses(_ lang: String? = nil) -> [String] {
        senses.filter { lang == nil || $0.lang == lang }.map(\.gloss)
    }
}

public final class Related: Equatable, @unchecked Sendable {
    public let lang: String
    public let word: String
    public let entryId: Int
    public internal(set) var score: Double
    public internal(set) var via: [String]
    public let reading: String?
    public internal(set) var glosses: [String] = []

    init(lang: String, word: String, entryId: Int, score: Double, via: [String],
         reading: String?) {
        self.lang = lang; self.word = word; self.entryId = entryId
        self.score = score; self.via = via; self.reading = reading
    }

    public static func == (a: Related, b: Related) -> Bool {
        a.lang == b.lang && a.word == b.word && a.entryId == b.entryId
            && a.score == b.score && a.via == b.via && a.reading == b.reading
            && a.glosses == b.glosses
    }
}

public struct Example: Equatable, Sendable {
    public let text: String
    public let trans: String?
    public let transLang: String?
    public let src: String

    public var handWritten: Bool { src.hasPrefix("folkets") }
}

public struct Inflection: Equatable, Sendable {
    public let form: String
    public let tags: String
    public let label: String
}

public struct Etymology: Equatable, Sendable {
    public let lang: String
    public let word: String
    public let pos: String?
    public let ipa: String?
    public let text: String?
    public let gloss: String?
}

public struct ImageInfo: Equatable, Sendable {
    public let word: String
    public let w: Int?
    public let h: Int?
    public let license: String?
    public let artist: String?
    public let page: String?
}

public struct AudioInfo: Equatable, Sendable {
    public let word: String
    public let accent: String?
    public let fname: String?
    public let bytes: Int?
}

public struct Suggestion: Hashable, Sendable {
    public let word: String
    public let lang: String
}

public struct LookupResult: Sendable {
    public let query: String
    public var entries: [Entry] = []
    public var related: [String: [Related]] = [:]
    public var etymologies: [Etymology] = []
    public var didYouMean: [Suggestion] = []
    public var definitions: [String: [String]] = [:]
    public var direct: [String: [String]] = [:]
    public var inflections: [String: [Inflection]] = [:]
    /// Set when the query was an inflected form and we resolved to its lemma,
    /// e.g. searching "ran" lands on "run".
    public var resolvedFrom: String?
    /// Set when the query is itself a headword AND an inflected form of a
    /// different lemma: "ran" is a Swedish noun, but also the past of "run".
    public var alsoFormOf: (lang: String, lemma: String, tags: String)?
    public var image: ImageInfo?
    public var audio: AudioInfo?
    /// Ordered: the headword's own language first, then any English top-up.
    public var examples: [(lang: String, items: [Example])] = []
    /// The bilingual core had nothing; the headword comes from Wiktionary
    /// alone, so translations (if any) are pivot-derived.
    public var wiktionaryOnly = false

    public var found: Bool { !entries.isEmpty }

    /// An empty result: nothing found for `query`.
    public init(query: String) {
        self.query = query
    }

    mutating func setExamples(_ lang: String, _ items: [Example]) {
        if let i = examples.firstIndex(where: { $0.lang == lang }) {
            examples[i].items = items
        } else {
            examples.append((lang, items))
        }
    }
}

// Ordered so a conjugation reads the way a textbook prints it.
private let TAG_ORDER: [Set<String>] = [
    ["infinitive"],
    ["present"], ["present", "singular", "third-person"],
    ["past"], ["past", "participle"], ["present", "participle"],
    ["supine"], ["imperative"], ["passive"], ["subjunctive"],
    ["plural"],
    ["indefinite", "singular"], ["definite", "singular"],
    ["indefinite", "plural"], ["definite", "plural"],
    ["genitive", "indefinite", "singular"], ["genitive", "definite", "singular"],
    ["genitive", "indefinite", "plural"], ["genitive", "definite", "plural"],
    ["positive"], ["comparative"], ["superlative"],
    ["neuter", "singular"], ["common-gender", "singular"],
]
private let TAG_LABEL: [(String, String)] = [
    ("present singular third-person", "he/she/it"),
    ("past participle", "past participle"),
    ("present participle", "-ing form"),
    ("indefinite singular", "singular"),
    ("definite singular", "the …"),
    ("indefinite plural", "plural"),
    ("definite plural", "the … (pl)"),
    ("genitive indefinite singular", "possessive"),
    ("supine", "supine"),
    ("imperative", "imperative"),
]

private func splitWords(_ s: String) -> [String] {
    s.split(whereSeparator: { $0 == " " || $0 == "\t" || $0 == "\n" }).map(String.init)
}

func tagLabel(_ tags: String) -> String {
    let t = splitWords(tags).sorted().joined(separator: " ")
    for (k, v) in TAG_LABEL where splitWords(k).sorted().joined(separator: " ") == t {
        return v
    }
    return tags.replacingOccurrences(of: "third-person", with: "3rd")
        .replacingOccurrences(of: "common-gender", with: "common")
}

func tagRank(_ tags: String) -> Int {
    let ts = Set(splitWords(tags))
    if let i = TAG_ORDER.firstIndex(of: ts) { return i }
    return 100 + ts.count
}

// Folkets uses Swedish grammar abbreviations; Wiktionary uses English names.
// Without this bridge the part-of-speech filter never matched and an
// adjective was shown conjugated like a verb.
private let POS_MAP = ["nn": "noun", "vb": "verb", "jj": "adj", "ab": "adv", "pp": "prep",
                       "pn": "pron", "nl": "num", "in": "intj", "kn": "conj",
                       "article": "article", "rg": "num", "hp": "pron"]

// Archaic orthography (long s) and similar clutter a learner's table.
private let ARCHAIC: [Character] = ["\u{017F}"]

extension Array {
    /// Python's `sorted` is stable; Swift's `sort` does not promise to be.
    func stableSorted(by less: (Element, Element) -> Bool) -> [Element] {
        enumerated().sorted { a, b in
            if less(a.element, b.element) { return true }
            if less(b.element, a.element) { return false }
            return a.offset < b.offset
        }.map(\.element)
    }
}

private func lexLess<T: Comparable>(_ a: [T], _ b: [T]) -> Bool { a.lexicographicallyPrecedes(b) }

/// Candidate headwords for the fuzzy fallback, bucketed by (script, length).
/// Every key in a bucket has the same length, so a bucket stores its keys as
/// one flat scalar buffer rather than a million small arrays.
struct FuzzBucket {
    let length: Int
    var scalars: [UInt32] = []
    var meta: [Suggestion] = []
}

public final class Lexicon: @unchecked Sendable {
    public let db: SQLiteDB
    private let lock = NSRecursiveLock()
    // The fuzzy pool takes seconds to build on a phone; it has its own lock
    // so autocomplete and lookups never wait behind it.
    private let fuzzLock = NSLock()
    private var fuzzCache: [String: FuzzBucket]?
    private var idfCache: [String: Double] = [:]
    private var probed: [String: Bool] = [:]

    public init(db: SQLiteDB) {
        self.db = db
    }

    /// False for a missing, truncated or foreign file.
    public var isUsable: Bool {
        (try? db.first("SELECT 1 FROM entry LIMIT 1")) != nil
            && (try? db.first("SELECT 1 FROM vocab LIMIT 1")) != nil
    }

    private func locked<T>(_ body: () throws -> T) rethrows -> T {
        lock.lock()
        defer { lock.unlock() }
        return try body()
    }

    private func q(_ sql: String, _ args: [SQLValue] = []) -> [Row] {
        (try? db.query(sql, args)) ?? []
    }

    // ---------------------------------------------------------------- helpers
    func loadEntries(_ ids: [Int], queryNorm: String?, cjkQuery: Bool) -> [Entry] {
        guard !ids.isEmpty else { return [] }
        let qm = SQLiteDB.marks(ids.count)
        let args = ids.map { SQLValue.of($0) }
        var byId: [Int: Entry] = [:]
        for r in q("SELECT id,lang,word,pos,extra FROM entry WHERE id IN (\(qm))", args) {
            let id = r.int("id")!
            var extra: [String: JSONValue] = [:]
            if case .object(let o)? = JSONValue.parse(r.string("extra")) { extra = o }
            byId[id] = Entry(id: id, lang: r.string("lang")!, word: r.string("word")!,
                             pos: r.string("pos"), extra: extra)
        }
        for s in q("SELECT entry_id,lang,gloss FROM sense WHERE entry_id IN (\(qm)) "
                   + "ORDER BY entry_id, ord", args) {
            if let id = s.int("entry_id"), byId[id] != nil {
                byId[id]!.senses.append(Sense(lang: s.string("lang")!, gloss: s.string("gloss")!))
            }
        }
        func rank(_ e: Entry) -> (Int, Int, Int, String) {
            // 1. A real headword match beats a synonym match. Without this,
            //    searching "hus" showed the entry for "byggnad", which merely
            //    lists hus as a synonym -- and then its declension table too.
            let isHead = (queryNorm != nil && Norm.norm(e.word) == queryNorm) ? 0 : 1
            // 2. Stay in the query's script. "ran" is pinyin for a rare hanzi,
            //    which otherwise outranked the English verb.
            let scriptOk = cjkQuery == (e.lang == "zh") ? 0 : 1
            // 3. Both sources list the most common sense first, so source
            //    order beats sense count for picking the primary entry.
            return (isHead, scriptOk, e.id, e.lang)
        }
        return byId.values.sorted { rank($0) < rank($1) }
    }

    /// Headword-only entries for a word the bilingual core lacks.
    ///
    /// Negative ids mark them as synthetic: nothing else in the database
    /// refers to them, and the wordbook stores a snapshot, not an id.
    func wiktionaryEntries(_ nq: String, _ query: String) -> [Entry] {
        let cjk = Norm.isCJK(query)
        let rows = q("SELECT lang, word, pos, ipa FROM wik WHERE norm=? "
                     + "ORDER BY (word = ?) DESC, "
                     + "         CASE lang WHEN 'en' THEN 0 WHEN 'sv' THEN 1 ELSE 2 END, "
                     + "         (lower(coalesce(pos,'')) IN ('name','proper noun')) ASC, id "
                     + "LIMIT 8", [.text(nq), .text(query)])
        var out: [Entry] = []
        var seen = Set<String>()
        for r in rows {
            let lang = r.string("lang")!, word = r.string("word")!, pos = r.string("pos")
            if (lang == "zh") != cjk { continue }
            let key = "\(lang)\u{1}\(word)\u{1}\(pos ?? "\u{2}")"
            if seen.contains(key) { continue }
            seen.insert(key)
            var extra: [String: JSONValue] = [:]
            if let ipa = r.string("ipa"), !ipa.isEmpty { extra["phonetic"] = .string(ipa) }
            out.append(Entry(id: -(out.count + 1), lang: lang, word: word, pos: pos, extra: extra))
            if out.count >= 4 { break }
        }
        return out
    }

    /// Probe rather than read sqlite_master: the table may live in an
    /// attached media pack, which sqlite_master for `main` does not list.
    func tableExists(_ name: String) -> Bool {
        (try? db.query("SELECT 1 FROM \(name) LIMIT 1")) != nil
    }

    private func mainTable(_ name: String) -> Bool {
        !q("SELECT name FROM sqlite_master WHERE type='table' AND name=?", [.text(name)]).isEmpty
    }

    private func probe(_ key: String, _ test: () -> Bool) -> Bool {
        if let v = probed[key] { return v }
        let v = test()
        probed[key] = v
        return v
    }

    /// Re-probe after a pack is installed at runtime.
    public func refreshMedia() {
        locked { probed.removeAll() }
    }

    /// Damp pivot terms that reach hundreds of entries. 'water' is a precise
    /// hinge; 'go' or 'thing' is not.
    func idf(_ enNorm: String) -> Double {
        if let v = idfCache[enNorm] { return v }
        let df = q("SELECT df FROM pivot_df WHERE en_norm=?", [.text(enNorm)]).first?.int("df") ?? 1
        let v = 1.0 / (1.0 + log(Double(max(df, 1))))
        if idfCache.count > 4096 { idfCache.removeAll() }
        idfCache[enNorm] = v
        return v
    }

    // ------------------------------------------------------------- autocomplete
    /// Prefix completions across all three languages, best-known first.
    public func suggest(_ prefix: String, limit: Int = 12) -> [Suggestion] {
        locked {
            let p = Norm.norm(prefix)
            guard !p.isEmpty else { return [] }
            let rows = q("SELECT norm,lang,disp,score,core FROM vocab "
                         + "WHERE norm >= ? AND norm < ? "
                         + "ORDER BY core DESC, (norm = ?) DESC, score DESC, length(norm) LIMIT ?",
                         [.text(p), .text(p + "\u{FFFF}"), .text(p), .of(limit * 6)])
            var out: [Suggestion] = []
            var seen = Set<Suggestion>()
            for r in rows {
                let s = Suggestion(word: r.string("disp")!, lang: r.string("lang")!)
                if seen.contains(s) { continue }
                seen.insert(s)
                out.append(s)
                if out.count >= limit { break }
            }
            return out
        }
    }

    // -------------------------------------------------------------- fuzzy match
    /// The length window does the heavy lifting: it keeps every pass small
    /// enough to feel instant, and it stops a six-letter typo from ever being
    /// "corrected" to the headword 'A'. A similarity cutoff alone does not
    /// achieve that - short strings score deceptively well.
    func fuzzPool() -> [String: FuzzBucket] {
        fuzzLock.lock()
        defer { fuzzLock.unlock() }
        if let p = fuzzCache { return p }
        var pool: [String: FuzzBucket] = [:]
        for r in q("SELECT norm, lang, disp FROM vocab WHERE core=1") {
            let n = r.string("norm")!
            let k = Norm.loose(n)
            if k.isEmpty { continue }
            let scalars = k.unicodeScalars.map(\.value)
            let key = "\(Norm.isCJK(n) ? "zh" : "la")\(scalars.count)"
            if pool[key] == nil { pool[key] = FuzzBucket(length: scalars.count) }
            pool[key]!.scalars.append(contentsOf: scalars)
            pool[key]!.meta.append(Suggestion(word: r.string("disp")!, lang: r.string("lang")!))
        }
        fuzzCache = pool
        return pool
    }

    /// Build the fuzzy pool ahead of time, so the first typo is answered
    /// without a pause. Safe to call from a background thread.
    public func warmUp() {
        _ = fuzzPool()
    }

    /// Nearest real headwords for a query that matched nothing.
    ///
    /// Stays inside the query's own script: a Latin typo should never be
    /// answered with Chinese characters.
    public func didYouMean(_ query: String, limit: Int = 8, window: Int = 2) -> [Suggestion] {
        do {
            let pool = fuzzPool()
            let lq = Norm.loose(query).unicodeScalars.map(\.value)
            guard !lq.isEmpty else { return [] }
            let script = Norm.isCJK(query) ? "zh" : "la"
            let scorer = Ratio(lq)
            var hits: [(Double, Suggestion)] = []
            for L in max(1, lq.count - window)...(lq.count + window) {
                guard let b = pool["\(script)\(L)"] else { continue }
                // rapidfuzz.process.extract: best `limit*2` at or above the
                // cutoff, ties in pool order.
                var bucketHits: [(Double, Int)] = []
                b.scalars.withUnsafeBufferPointer { buf in
                    for i in 0..<b.meta.count {
                        let s = scorer.score(UnsafeBufferPointer(rebasing: buf[(i * L)..<((i + 1) * L)]))
                        if s >= 60 { bucketHits.append((s, i)) }
                    }
                }
                bucketHits = bucketHits.stableSorted { $0.0 > $1.0 }
                for (s, i) in bucketHits.prefix(limit * 2) { hits.append((s, b.meta[i])) }
            }
            hits = hits.stableSorted { $0.0 > $1.0 }
            var out: [Suggestion] = []
            var seen = Set<Suggestion>()
            for (_, m) in hits {
                if seen.contains(m) { continue }
                seen.insert(m)
                out.append(m)
                if out.count >= limit { break }
            }
            return out
        }
    }

    // ------------------------------------------------------------- definitions
    /// Monolingual definitions from Wiktionary, for the Definition block.
    ///
    /// Folkets and CC-CEDICT give *translations*; this gives what the word
    /// actually means in its own language.
    func definitionsFor(_ lang: String, _ word: String, limit: Int = 6) -> [String] {
        var out: [String] = []
        for r in q("SELECT gloss FROM wik WHERE lang=? AND norm=? AND gloss IS NOT NULL "
                   + "ORDER BY id LIMIT 8", [.text(lang), .text(Norm.norm(word))]) {
            for g in r.string("gloss")!.components(separatedBy: " | ") {
                let g = g.trimmingCharacters(in: .whitespacesAndNewlines)
                if !g.isEmpty && !out.contains(g) { out.append(g) }
            }
        }
        return Array(out.prefix(limit))
    }

    // --------------------------------------------------------------- examples
    public var hasExamples: Bool { locked { probe("example") { mainTable("example") } } }

    /// Sentences illustrating a headword, best first.
    ///
    /// `score` puts hand-written dictionary examples ahead of corpus ones,
    /// and shorter sentences ahead of longer ones.
    func examplesFor(_ lang: String, _ word: String, limit: Int = 6) -> [Example] {
        guard hasExamples, LANGS.contains(lang) else { return [] }
        // English has both Swedish- and Chinese-translated examples. Lead with
        // Swedish for a Latin-script headword and Chinese for a CJK one, so the
        // pairing matches what the reader is most likely comparing.
        let prefer = lang == "zh" ? "zh" : "sv"
        let rows = q("SELECT text, trans, trans_lang, src FROM example "
                     + "WHERE lang=? AND norm=? "
                     + "ORDER BY score + CASE WHEN trans_lang IS NULL THEN 1.0 "
                     + "                      WHEN trans_lang = ? THEN 0.0 "
                     + "                      ELSE 1.5 END, length(text) LIMIT ?",
                     [.text(lang), .text(Norm.norm(word)), .text(prefer), .of(limit * 3)])
        var out: [Example] = []
        var seen = Set<String>()
        for r in rows {
            let text = r.string("text")!
            if seen.contains(text) { continue }
            seen.insert(text)
            out.append(Example(text: text, trans: r.string("trans"),
                               transLang: r.string("trans_lang"), src: r.string("src")!))
            if out.count >= limit { break }
        }
        return out
    }

    // ------------------------------------------------------------------ audio
    public var hasAudio: Bool { locked { probe("audio") { tableExists("audio") } } }

    /// Metadata only; the clip itself is fetched when actually played.
    func audioFor(_ lang: String, _ word: String) -> AudioInfo? {
        guard hasAudio, let r = q("SELECT word, accent, fname, bytes FROM audio WHERE lang=? AND norm=?",
                                  [.text(lang), .text(Norm.norm(word))]).first else { return nil }
        return AudioInfo(word: r.string("word")!, accent: r.string("accent"),
                         fname: r.string("fname"), bytes: r.int("bytes"))
    }

    public func audioBlob(_ lang: String, _ word: String) -> Data? {
        locked {
            guard hasAudio else { return nil }
            return q("SELECT data FROM audio WHERE lang=? AND norm=?",
                     [.text(lang), .text(Norm.norm(word))]).first?.data("data")
        }
    }

    // ----------------------------------------------------------------- images
    public var hasImages: Bool { locked { probe("image") { tableExists("image") } } }

    /// Metadata only; the blob is fetched lazily when the view asks.
    func imageFor(_ lang: String, _ word: String) -> ImageInfo? {
        guard hasImages, let r = q("SELECT word,w,h,license,artist,page FROM image WHERE lang=? AND norm=?",
                                   [.text(lang), .text(Norm.norm(word))]).first else { return nil }
        return ImageInfo(word: r.string("word")!, w: r.int("w"), h: r.int("h"),
                         license: r.string("license"), artist: r.string("artist"),
                         page: r.string("page"))
    }

    public func imageBlob(_ lang: String, _ word: String) -> Data? {
        locked {
            guard hasImages else { return nil }
            return q("SELECT data FROM image WHERE lang=? AND norm=?",
                     [.text(lang), .text(Norm.norm(word))]).first?.data("data")
        }
    }

    // ------------------------------------------------------------ inflections
    public var hasForms: Bool { locked { probe("wform") { mainTable("wform") } } }

    /// Conjugation / declension table for a headword.
    func inflectionsFor(_ lang: String, _ word: String, pos: String?, limit: Int = 30) -> [Inflection] {
        guard hasForms, lang != "zh" else { return [] }
        var rows = q("SELECT form, tags, pos FROM wform WHERE lang=? AND lemma_norm=?",
                     [.text(lang), .text(Norm.norm(word))])
        if let want = POS_MAP[(pos ?? "").lowercased()] {
            let same = rows.filter { ($0.string("pos") ?? "").lowercased() == want }
            if !same.isEmpty { rows = same }
        }
        var seen = Set<String>()
        var out: [Inflection] = []
        for r in rows {
            let form = r.string("form")!, tags = r.string("tags")!
            if form.contains(where: { ARCHAIC.contains($0) }) { continue }
            let label = tagLabel(tags)
            let key = "\(form)\u{1}\(label)"
            if seen.contains(key) { continue }
            seen.insert(key)
            out.append(Inflection(form: form, tags: tags, label: label))
        }
        out.sort { a, b in
            let ra = tagRank(a.tags), rb = tagRank(b.tags)
            if ra != rb { return ra < rb }
            return lexLess(Array(a.form.unicodeScalars.map(\.value)),
                           Array(b.form.unicodeScalars.map(\.value)))
        }
        return Array(out.prefix(limit))
    }

    /// Map an inflected form back to its lemma: 'ran' -> ('en','run').
    func lemmaOf(_ surface: String, preferLang: String? = nil) -> (lang: String, lemma: String, tags: String)? {
        guard hasForms else { return nil }
        let rows = q("SELECT lang, lemma, tags FROM wform WHERE norm=? LIMIT 30",
                     [.text(Norm.norm(surface))]).filter { r in
            let lemma = r.string("lemma")!
            return !Norm.isUpper(lemma)                // skip acronyms: HU, US
                && Norm.length(lemma) > 1
                && (preferLang == nil || r.string("lang") == preferLang)
        }
        guard let first = rows.first else { return nil }
        // Prefer a lemma that actually exists in the bilingual core, so we
        // resolve to something we can show translations for.
        for r in rows where !q("SELECT 1 FROM form WHERE norm=? LIMIT 1",
                               [.text(Norm.norm(r.string("lemma")!))]).isEmpty {
            return (r.string("lang")!, r.string("lemma")!, r.string("tags")!)
        }
        return (first.string("lang")!, first.string("lemma")!, first.string("tags")!)
    }

    private func entryIds(_ n: String) -> [Int] {
        q("SELECT DISTINCT entry_id FROM form WHERE norm=?", [.text(n)]).compactMap { $0.int("entry_id") }
    }

    // ------------------------------------------------------------------ lookup
    public func lookup(_ query: String, maxRelated: Int = 18) -> LookupResult {
        locked { lookupLocked(query, maxRelated: maxRelated) }
    }

    private func lookupLocked(_ query: String, maxRelated: Int) -> LookupResult {
        var res = LookupResult(query: query)
        var nq = Norm.norm(query)
        guard !nq.isEmpty else { return res }
        let cjk = Norm.isCJK(query)
        var ids = entryIds(nq)
        let onlyPinyin = !ids.isEmpty && !cjk && q(
            "SELECT DISTINCT e.lang FROM form f JOIN entry e ON e.id=f.entry_id WHERE f.norm=?",
            [.text(nq)]).allSatisfy { $0.string("lang") == "zh" }
        if ids.isEmpty || onlyPinyin {
            // Not a headword - maybe it is an inflected form ("ran", "husen").
            if let hit = lemmaOf(query) {
                let lemmaIds = entryIds(Norm.norm(hit.lemma))
                if !lemmaIds.isEmpty {
                    res.resolvedFrom = query
                    // Keep any pinyin hits too, but the lemma now leads.
                    nq = Norm.norm(hit.lemma)
                    ids = lemmaIds + ids.filter { !lemmaIds.contains($0) }
                }
            }
        }
        if !ids.isEmpty {
            res.entries = loadEntries(ids, queryNorm: nq, cjkQuery: cjk)
        } else {
            // Not in Folkets or CC-CEDICT at all. Wiktionary knows a million
            // English lemmas to Folkets' 56k, so show its definition,
            // pronunciation and etymology - and whatever the English pivot can
            // reach - rather than a bare "not in the dictionary".
            res.entries = wiktionaryEntries(nq, query)
            res.wiktionaryOnly = !res.entries.isEmpty
        }

        // Query-side pivot weights: which English terms does this word mean?
        var qpTerms: [String] = []
        var qp: [String: Double] = [:]
        if !ids.isEmpty {
            for r in q("SELECT en_norm, max(weight) w FROM pivot "
                       + "WHERE entry_id IN (\(SQLiteDB.marks(ids.count))) GROUP BY en_norm",
                       ids.map { .of($0) }) {
                let t = r.string("en_norm")!
                if qp[t] == nil { qpTerms.append(t) }
                qp[t] = r.double("w") ?? 0
            }
        }
        // A word that is itself an English pivot term counts as its own hinge.
        if !cjk && !q("SELECT 1 FROM pivot_df WHERE en_norm=?", [.text(nq)]).isEmpty {
            if qp[nq] == nil { qpTerms.append(nq) }
            qp[nq] = max(qp[nq] ?? 0, 1.0)
        }
        if !qp.isEmpty {
            res.related = related(terms: qpTerms, weights: qp, exclude: Set(ids), cap: maxRelated)
        }
        // Direct translations carried on the entry itself always beat
        // pivot-derived ones; collect them per target language.
        for e in res.entries.prefix(4) {
            for sn in e.senses where sn.lang != e.lang {
                if !(res.direct[sn.lang] ?? []).contains(sn.gloss) {
                    res.direct[sn.lang, default: []].append(sn.gloss)
                }
            }
        }
        for e in res.entries.prefix(3) where res.definitions[e.lang] == nil {
            let d = definitionsFor(e.lang, e.word)
            if !d.isEmpty { res.definitions[e.lang] = d }
        }
        if !res.entries.isEmpty && res.resolvedFrom == nil && !cjk,
           let hit = lemmaOf(query, preferLang: res.entries[0].lang),
           Norm.norm(hit.lemma) != nq {
            res.alsoFormOf = hit
        }
        if let e = res.entries.first, e.lang != "zh" {
            let infl = inflectionsFor(e.lang, e.word, pos: e.pos)
            if !infl.isEmpty { res.inflections[e.lang] = infl }
        }
        if let e0 = res.entries.first {
            res.audio = audioFor(e0.lang, e0.word)
            res.image = imageFor(e0.lang, e0.word)
            if res.image == nil {
                // Illustrations are keyed on English headwords. For a Swedish
                // or Chinese query, try its direct English translations first
                // (authoritative) before pivot-derived ones.
                let cands = Array((res.direct["en"] ?? []).prefix(3))
                    + (res.related["en"] ?? []).prefix(3).map(\.word)
                for c in cands {
                    res.image = imageFor("en", c)
                    if res.image != nil { break }
                }
            }
            let ex = examplesFor(e0.lang, e0.word)
            if !ex.isEmpty { res.setExamples(e0.lang, ex) }
            // Chinese has its own corpus, but coverage is thinner than
            // English, so top up from the English equivalent when sparse.
            if ex.count < 3 {
                for c in (res.direct["en"] ?? []).prefix(2) {
                    let exEn = examplesFor("en", c)
                    if !exEn.isEmpty {
                        res.setExamples("en", exEn)
                        break
                    }
                }
            }
        }
        if res.entries.isEmpty {
            res.didYouMean = didYouMean(query)
        }
        res.etymologies = etymologies(res)
        return res
    }

    private func related(terms qpTerms: [String], weights qp: [String: Double],
                         exclude: Set<Int>, cap: Int) -> [String: [Related]] {
        let terms = Array(qpTerms.stableSorted { qp[$0]! > qp[$1]! }.prefix(60))
        let rows = q("SELECT p.entry_id, p.en_norm, p.weight, e.lang, e.word, e.extra "
                     + "FROM pivot p JOIN entry e ON e.id = p.entry_id "
                     + "WHERE p.en_norm IN (\(SQLiteDB.marks(terms.count)))",
                     terms.map { .text($0) })
        var agg: [Int: Related] = [:]
        var order: [Int] = []
        for r in rows {
            let eid = r.int("entry_id")!
            if exclude.contains(eid) { continue }
            let en = r.string("en_norm")!
            let s = qp[en]! * (r.double("weight") ?? 0) * idf(en)
            let cur: Related
            if let c = agg[eid] {
                cur = c
            } else {
                let extra = JSONValue.parse(r.string("extra"))
                cur = Related(lang: r.string("lang")!, word: r.string("word")!, entryId: eid,
                              score: 0, via: [],
                              reading: extra?["pinyin"]?.nonEmptyString
                                ?? extra?["phonetic"]?.nonEmptyString)
                agg[eid] = cur
                order.append(eid)
            }
            if s > cur.score { cur.score = s }
            if !cur.via.contains(en) { cur.via.append(en) }
        }
        var out: [String: [Related]] = [:]
        for lg in LANGS {
            let items = order.compactMap { agg[$0] }.filter { $0.lang == lg }
                .stableSorted { a, b in
                    if a.score != b.score { return a.score > b.score }
                    return Norm.length(a.word) < Norm.length(b.word)
                }
            // Collapse duplicate headwords (Folkets lists homographs separately).
            var seen = Set<String>()
            var keep: [Related] = []
            for it in items {
                if seen.contains(it.word) { continue }
                seen.insert(it.word)
                it.via = Array(it.via.stableSorted { qp[$0]! > qp[$1]! }.prefix(3))
                keep.append(it)
                if keep.count >= cap { break }
            }
            if !keep.isEmpty {
                attachGlosses(keep)
                out[lg] = keep
            }
        }
        return out
    }

    private func attachGlosses(_ rels: [Related]) {
        let ids = rels.map(\.entryId)
        var by: [Int: [String]] = [:]
        for s in q("SELECT entry_id, gloss FROM sense WHERE entry_id IN (\(SQLiteDB.marks(ids.count))) "
                   + "ORDER BY entry_id, ord", ids.map { .of($0) }) {
            by[s.int("entry_id")!, default: []].append(s.string("gloss")!)
        }
        for r in rels { r.glosses = Array((by[r.entryId] ?? []).prefix(4)) }
    }

    // -------------------------------------------------------------- etymology
    func etymologyFor(_ lang: String, _ word: String) -> [Etymology] {
        q("SELECT w.lang,w.word,w.pos,w.ipa,w.gloss,e.txt "
          + "FROM wik w LEFT JOIN etym e ON e.id=w.etym_id "
          + "WHERE w.lang=? AND w.norm=? "
          // Prefer the ordinary word: exact spelling first, then common nouns
          // over proper nouns -- otherwise 'water' resolves to the surname.
          + "ORDER BY (w.word = ?) DESC, "
          + "         (lower(coalesce(w.pos,'')) IN ('name','proper noun')) ASC, "
          + "         (e.txt IS NULL) ASC, w.id LIMIT 6",
          [.text(lang), .text(Norm.norm(word)), .text(word)]).map { r in
            Etymology(lang: r.string("lang")!, word: r.string("word")!, pos: r.string("pos"),
                      ipa: r.string("ipa"), text: r.string("txt"), gloss: r.string("gloss"))
        }
    }

    /// Etymology for the searched word plus the best equivalent in each of
    /// the other two languages - so one search explains all three.
    private func etymologies(_ res: LookupResult) -> [Etymology] {
        var want: [(String, String)] = []
        var seen = Set<String>()
        for e in res.entries {
            let k = "\(e.lang)\u{1}\(Norm.norm(e.word))"
            if !seen.contains(k) {
                seen.insert(k)
                want.append((e.lang, e.word))
            }
        }
        for lg in LANGS {
            for r in (res.related[lg] ?? []).prefix(1) {
                let k = "\(lg)\u{1}\(Norm.norm(r.word))"
                if !seen.contains(k) {
                    seen.insert(k)
                    want.append((lg, r.word))
                }
            }
        }
        var out: [Etymology] = []
        for (lg, w) in want {
            let hits = etymologyFor(lg, w)
            if let withText = hits.first(where: { !($0.text ?? "").isEmpty }) {
                out.append(withText)
            } else if let first = hits.first {
                out.append(first)
            }
        }
        return out
    }
}

/// rapidfuzz's `fuzz.ratio`: normalised Indel similarity, 0-100, computed
/// as 200·LCS/(len1+len2). LCS uses the bit-parallel algorithm (Hyyrö), so
/// a query of up to 64 code points costs one word operation per character.
struct Ratio {
    let query: [UInt32]
    let uniq: [UInt32]
    let masks: [UInt64]

    init(_ query: [UInt32]) {
        self.query = query
        var u: [UInt32] = [], m: [UInt64] = []
        if query.count <= 64 {
            for (i, c) in query.enumerated() {
                if let j = u.firstIndex(of: c) { m[j] |= 1 << UInt64(i) }
                else { u.append(c); m.append(1 << UInt64(i)) }
            }
        }
        uniq = u
        masks = m
    }

    func lcs(_ s: UnsafeBufferPointer<UInt32>) -> Int {
        let n = query.count
        if n > 64 {
            // Plain dynamic programme; queries this long are rare.
            var prev = [Int](repeating: 0, count: s.count + 1)
            var cur = prev
            for i in 0..<n {
                for j in 0..<s.count {
                    cur[j + 1] = query[i] == s[j] ? prev[j] + 1 : max(prev[j + 1], cur[j])
                }
                swap(&prev, &cur)
            }
            return prev[s.count]
        }
        var v: UInt64 = ~0
        for c in s {
            var pm: UInt64 = 0
            for j in 0..<uniq.count where uniq[j] == c { pm = masks[j]; break }
            let u = v & pm
            v = (v &+ u) | (v &- u)
        }
        let live: UInt64 = n == 64 ? ~0 : ((1 << UInt64(n)) - 1)
        return (~v & live).nonzeroBitCount
    }

    func score(_ s: UnsafeBufferPointer<UInt32>) -> Double {
        // Same floating-point steps as rapidfuzz, so ties and the cutoff
        // land exactly where the desktop's do.
        let total = query.count + s.count
        if total == 0 { return 100 }
        let dist = total - 2 * lcs(s)
        return (1.0 - Double(dist) / Double(total)) * 100
    }
}
