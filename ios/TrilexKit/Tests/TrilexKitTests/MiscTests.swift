import Foundation
import XCTest
@testable import TrilexKit

final class SHA256Tests: XCTestCase {
    func hex(_ s: String) -> String {
        var d = Digest256()
        d.update(Data(s.utf8))
        return d.hex()
    }

    func testKnownVectors() {
        XCTAssertEqual(hex(""), "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
        XCTAssertEqual(hex("abc"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        XCTAssertEqual(hex("abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"),
                       "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1")
    }

    func testIncrementalMatchesOneShot() {
        let data = Data((0..<10_000).map { UInt8($0 % 251) })
        var a = Digest256()
        a.update(data)
        var b = Digest256()
        var i = 0
        for n in [1, 63, 64, 65, 1000, 7] + Array(repeating: 1024, count: 9) where i < data.count {
            b.update(data.subdata(in: i..<min(data.count, i + n)))
            i += n
        }
        if i < data.count { b.update(data.subdata(in: i..<data.count)) }
        XCTAssertEqual(a.hex(), b.hex())
    }
}

final class PageTests: XCTestCase {
    func testSwedishWordPage() throws {
        let page = Page(try Fixture.lexicon().lookup("husen"))
        XCTAssertTrue(page.found)
        XCTAssertEqual(page.resolvedFrom, "husen")
        XCTAssertEqual(page.header?.entry.word, "hus")
        XCTAssertEqual(page.header?.langName, "Svenska")
        XCTAssertEqual(page.definitions, ["byggnad att bo i"])
        XCTAssertEqual(page.forms.map(\.label), ["the …", "plural", "the … (pl)", "genitive definite singular"])
        XCTAssertEqual(page.translations.map(\.lang), ["zh", "en"])
        let zh = page.translations[0]
        XCTAssertEqual(zh.note, Page.throughEnglishNote)    // no direct sv-zh source
        XCTAssertEqual(zh.items.first?.word, "房子")
        XCTAssertEqual(zh.items.first?.via, ["building", "house"])
        XCTAssertEqual(page.translations[1].items.first?.word, "house")
        let ex = try XCTUnwrap(page.examples.first)
        XCTAssertEqual(ex.title, "Examples")
        XCTAssertEqual(ex.lines[0].segments, [Segment(text: "Huset", bold: true),
                                              Segment(text: " är stort.", bold: false)])
    }

    func testNotFoundPage() throws {
        let page = Page(try Fixture.lexicon().lookup("watter"))
        XCTAssertFalse(page.found)
        XCTAssertEqual(page.didYouMean.first, Suggestion(word: "water", lang: "en"))
        XCTAssertNil(page.header)
    }

    func testHeaderMedia() throws {
        let page = Page(try Fixture.lexicon().lookup("vatten"))
        XCTAssertEqual(page.header?.audio?.word, "vatten")
        // Illustrations are keyed on English; Swedish borrows its translation's.
        XCTAssertEqual(page.header?.image?.word, "water")
        XCTAssertEqual(page.header?.imageCredit, "A. Photographer · CC BY-SA 4.0")
        XCTAssertEqual(page.examples.first?.title, "Examples (English)")
    }

    func testHighlight() {
        XCTAssertEqual(highlight("Running is fun; I run daily.", forms: ["run", "running"]),
                       [Segment(text: "Running", bold: true), Segment(text: " is fun; I run daily.", bold: false)])
        XCTAssertEqual(highlight("rerun it, run, run, run", forms: ["run"]).filter(\.bold).count, 2)
        XCTAssertEqual(highlight("nothing here", forms: ["x"]), [Segment(text: "nothing here", bold: false)])
    }
}

final class PackTests: XCTestCase {
    func testManifestParsing() {
        let client = PackClient(paths: TrilexPaths(base: Fixture.tempDir()))
        let m = client.parse([
            "packs": [["name": "audio", "title": "Pronunciation audio", "detail": "d",
                       "file": "media-audio.db", "rows": 16514, "bytes": 81_000_000, "sha256": "ab"],
                      ["name": "broken"]],
            "dictionary": ["file": "dict.db.xz", "bytes": 222_000_000, "sha256": "cd",
                           "url": "https://example.test/dict.db.xz"],
        ])
        XCTAssertEqual(m.packs.map(\.name), ["audio"])
        XCTAssertEqual(m.packs[0].url, "\(DEFAULT_PACK_URL)/media-audio.db")
        XCTAssertEqual(m.preferredDictionary?.url, "https://example.test/dict.db.xz")
        XCTAssertNil(m.mobileDictionary)

        let api = PackClient(baseURL: "https://example.test/thriauga/", paths: client.paths)
        XCTAssertEqual(api.manifestURL, "https://example.test/thriauga/v1/packs")
        XCTAssertEqual(api.parse(["packs": [["name": "images", "file": "media-images.db", "bytes": 5]],
                                  "dictionary_mobile": ["file": "dict-mobile.db.xz", "bytes": 1]])
            .preferredDictionary?.url, "https://example.test/thriauga/v1/dictionary")
    }

    func testAttachingAPackLightsUpAudio() throws {
        let dir = Fixture.tempDir()
        let paths = TrilexPaths(base: dir)
        // A dictionary with no media of its own.
        let core = try SQLiteDB(path: paths.dictionary.path)
        try core.execute(String(contentsOf: Fixture.url("dict", "sql"), encoding: .utf8))
        try core.execute("DROP TABLE audio; DROP TABLE image;")
        let lex = try Lexicon.open(at: paths.dictionary, packsIn: paths)
        XCTAssertFalse(lex.hasAudio)
        XCTAssertNil(lex.lookup("water").audio)

        // Install a pack while "running", as the add-ons screen does.
        let pack = try SQLiteDB(path: paths.pack("audio").path)
        try pack.execute("""
            CREATE TABLE audio(lang TEXT NOT NULL, norm TEXT NOT NULL, word TEXT NOT NULL,
              data BLOB NOT NULL, bytes INTEGER, fname TEXT, accent TEXT,
              PRIMARY KEY(lang, norm)) WITHOUT ROWID;
            """)
        try pack.run("INSERT INTO audio VALUES('en','water','water',?,4,'f.ogg','UK')",
                     [.blob(Data([1, 2, 3, 4]))])
        // Pad past the "is this a real pack" size floor.
        try pack.execute("CREATE TABLE pad(x BLOB); INSERT INTO pad VALUES(zeroblob(8192));")
        XCTAssertTrue(lex.attachPack("audio", paths: paths))
        XCTAssertEqual(lex.lookup("water").audio?.accent, "UK")
        XCTAssertEqual(lex.audioBlob("en", "water"), Data([1, 2, 3, 4]))
        XCTAssertTrue(lex.detachPack("audio"))
        XCTAssertNil(lex.lookup("water").audio)
    }

    func testFuzzyFallbackScalesToALargePool() throws {
        // Not a benchmark; a guard against an accidental quadratic.
        let db = try SQLiteDB(path: ":memory:")
        try db.execute(String(contentsOf: Fixture.url("dict", "sql"), encoding: .utf8))
        try db.transaction {
            for i in 0..<50_000 {
                try db.run("INSERT OR IGNORE INTO vocab VALUES(?, 'en', ?, 0, 1)",
                           [.text("w\(i)ord"), .text("w\(i)ord")])
            }
        }
        let lex = Lexicon(db: db)
        lex.warmUp()
        let t0 = Date()
        XCTAssertEqual(lex.didYouMean("watter").first, Suggestion(word: "water", lang: "en"))
        XCTAssertLessThan(Date().timeIntervalSince(t0), 2.0)
    }
}
