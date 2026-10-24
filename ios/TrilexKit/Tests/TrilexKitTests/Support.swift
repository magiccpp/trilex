import Foundation
import XCTest
@testable import TrilexKit

enum Fixture {
    static func url(_ name: String, _ ext: String) -> URL {
        Bundle.module.url(forResource: name, withExtension: ext, subdirectory: "Fixtures")!
    }

    static var expected: JSONValue {
        let data = try! Data(contentsOf: url("expected", "json"))
        return try! JSONDecoder().decode(JSONValue.self, from: data)
    }

    /// The fixture dictionary, loaded fresh into memory.
    static func lexicon() throws -> Lexicon {
        let db = try SQLiteDB(path: ":memory:")
        try db.execute(String(contentsOf: url("dict", "sql"), encoding: .utf8))
        return Lexicon(db: db)
    }

    static func tempDir() -> URL {
        let u = FileManager.default.temporaryDirectory
            .appendingPathComponent("trilex-tests-\(UUID().uuidString)", isDirectory: true)
        try! FileManager.default.createDirectory(at: u, withIntermediateDirectories: true)
        return u
    }
}

extension JSONValue {
    var number: Double? {
        if case .number(let d) = self { return d }
        return nil
    }

    var object: [String: JSONValue]? {
        if case .object(let o) = self { return o }
        return nil
    }
}

/// Structural equality with a tolerance for floating point, reporting the
/// path of the first difference.
func assertJSONEqual(_ a: JSONValue, _ b: JSONValue, path: String = "$",
                     file: StaticString = #filePath, line: UInt = #line) {
    switch (a, b) {
    case (.number(let x), .number(let y)):
        XCTAssertEqual(x, y, accuracy: 1e-9 * max(1, abs(y)), "at \(path)", file: file, line: line)
    case (.array(let x), .array(let y)):
        XCTAssertEqual(x.count, y.count, "length at \(path): \(a.serialized()) vs \(b.serialized())",
                       file: file, line: line)
        for (i, (p, q)) in zip(x, y).enumerated() {
            assertJSONEqual(p, q, path: "\(path)[\(i)]", file: file, line: line)
        }
    case (.object(let x), .object(let y)):
        XCTAssertEqual(Set(x.keys), Set(y.keys), "keys at \(path)", file: file, line: line)
        for k in x.keys.sorted() where y[k] != nil {
            assertJSONEqual(x[k]!, y[k]!, path: "\(path).\(k)", file: file, line: line)
        }
    default:
        XCTAssertEqual(a, b, "at \(path)", file: file, line: line)
    }
}

/// The same shape make_fixture.py writes for a desktop result.
func json(_ r: Result) -> JSONValue {
    func s(_ v: String?) -> JSONValue { .of(v) }
    func i(_ v: Int?) -> JSONValue { v.map { .number(Double($0)) } ?? .null }
    func strs(_ v: [String]) -> JSONValue { .array(v.map { .string($0) }) }
    return .object([
        "query": .string(r.query),
        "entries": .array(r.entries.map { e in
            .object(["id": i(e.id), "lang": .string(e.lang), "word": .string(e.word), "pos": s(e.pos),
                     "reading": s(e.reading), "traditional": s(e.traditional),
                     "senses": .array(e.senses.map { .array([.string($0.lang), .string($0.gloss)]) })])
        }),
        "related": .object(r.related.mapValues { rs in
            .array(rs.map { x in
                .object(["word": .string(x.word), "entry_id": i(x.entryId), "score": .number(x.score),
                         "via": strs(x.via), "reading": s(x.reading), "glosses": strs(x.glosses)])
            })
        }),
        "etymologies": .array(r.etymologies.map {
            .array([.string($0.lang), .string($0.word), s($0.pos), s($0.ipa), s($0.text), s($0.gloss)])
        }),
        "did_you_mean": .array(r.didYouMean.map { .array([.string($0.word), .string($0.lang)]) }),
        "definitions": .object(r.definitions.mapValues(strs)),
        "direct": .object(r.direct.mapValues(strs)),
        "inflections": .object(r.inflections.mapValues { v in
            .array(v.map { .array([.string($0.form), .string($0.tags), .string($0.label)]) })
        }),
        "resolved_from": s(r.resolvedFrom),
        "also_form_of": r.alsoFormOf.map { .array([.string($0.lang), .string($0.lemma), .string($0.tags)]) } ?? .null,
        "image": r.image.map { im in
            .object(["word": .string(im.word), "w": i(im.w), "h": i(im.h), "license": s(im.license),
                     "artist": s(im.artist), "page": s(im.page)])
        } ?? .null,
        "audio": r.audio.map { a in
            .object(["word": .string(a.word), "accent": s(a.accent), "fname": s(a.fname), "bytes": i(a.bytes)])
        } ?? .null,
        "examples": .array(r.examples.map { ex in
            .array([.string(ex.lang), .array(ex.items.map {
                .array([.string($0.text), s($0.trans), s($0.transLang), .string($0.src)])
            })])
        }),
        "wiktionary_only": .bool(r.wiktionaryOnly),
    ])
}
