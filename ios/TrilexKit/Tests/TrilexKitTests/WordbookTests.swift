import XCTest
@testable import TrilexKit

/// A wordbook whose clock the test controls.
final class TimeMachine {
    var now = Date(timeIntervalSince1970: 1_790_000_000)
    func travel(days: Int) { now = now.addingTimeInterval(Double(days) * 86_400) }
}

final class WordbookTests: XCTestCase {
    func makeBook(_ clock: TimeMachine, path: String = ":memory:") throws -> Wordbook {
        try Wordbook(db: SQLiteDB(path: path), now: { clock.now })
    }

    func testAddGradeAndSchedule() throws {
        let clock = TimeMachine()
        let book = try makeBook(clock)
        let lex = try Fixture.lexicon()
        let res = lex.lookup("vatten")
        let card = try XCTUnwrap(book.add(lang: "sv", word: "vatten", snapshot: res.snapshot))
        XCTAssertTrue(card.isNew)
        XCTAssertEqual(card.meaning(), "water")
        XCTAssertEqual(book.counts(), WordbookCounts(total: 1, due: 1, new: 1, review: 0))

        let preview = book.preview(card)
        XCTAssertEqual(preview[.again], 1.0)                       // floor of one day
        XCTAssertGreaterThan(preview[.easy]!, preview[.good]!)

        let after = try XCTUnwrap(book.grade(card, .good))
        XCTAssertFalse(after.isNew)
        XCTAssertEqual(after.reps, 1)
        XCTAssertEqual(after.lastReview, book.today)
        XCTAssertEqual(after.due, Day.adding(Int(after.interval.rounded(.toNearestOrEven)), to: book.today))
        XCTAssertEqual(book.counts().due, 0)
        XCTAssertNotNil(book.nextDue())

        clock.travel(days: 30)
        XCTAssertEqual(book.counts().due, 1)
        XCTAssertLessThan(after.retention(today: book.today), 0.9)
    }

    func testDueQueueIsMostForgottenFirst() throws {
        let clock = TimeMachine()
        let book = try makeBook(clock)
        for w in ["a", "b", "c"] { try book.add(lang: "en", word: w) }
        try book.grade(book.get(lang: "en", word: "a")!, .easy)   // long memory
        try book.grade(book.get(lang: "en", word: "b")!, .again)  // fragile memory
        clock.travel(days: 60)
        try book.add(lang: "en", word: "d")
        // New cards first, then lowest predicted recall.
        XCTAssertEqual(book.due().map(\.word), ["c", "d", "b", "a"])
    }

    func testRemoveIsATombstoneAndReAddRevives() throws {
        let book = try makeBook(TimeMachine())
        let c = try XCTUnwrap(book.add(lang: "en", word: "water"))
        try book.remove(c.id)
        XCTAssertFalse(book.has(lang: "en", word: "water"))
        XCTAssertTrue(book.all().isEmpty)
        XCTAssertEqual(try book.get(lang: "en", word: "water")?.deleted, true)
        let back = try XCTUnwrap(book.add(lang: "en", word: "water"))
        XCTAssertEqual(back.uid, c.uid)
        XCTAssertFalse(back.deleted)
    }

    func testDesktopWordbookOpensAndMigrates() throws {
        // A wordbook from before sync existed: no uid, no FSRS columns.
        let dir = Fixture.tempDir()
        let path = dir.appendingPathComponent("user.db").path
        let old = try SQLiteDB(path: path)
        try old.execute("""
            CREATE TABLE card(id INTEGER PRIMARY KEY, lang TEXT NOT NULL, word TEXT NOT NULL,
              note TEXT DEFAULT '', snapshot TEXT, added_at TEXT NOT NULL, due TEXT NOT NULL,
              interval REAL NOT NULL DEFAULT 0, ease REAL NOT NULL DEFAULT 2.5,
              reps INTEGER NOT NULL DEFAULT 0, lapses INTEGER NOT NULL DEFAULT 0, UNIQUE(lang, word));
            INSERT INTO card(lang,word,added_at,due) VALUES('sv','hus','2024-01-01','2024-01-02');
            """)
        let book = try Wordbook(db: SQLiteDB(path: path))
        let c = try XCTUnwrap(book.get(lang: "sv", word: "hus"))
        XCTAssertFalse(c.uid.isEmpty)
        XCTAssertFalse(c.updatedAt.isEmpty)
        XCTAssertEqual(book.counts().due, 1)
    }

    func testCSVExport() throws {
        let book = try makeBook(TimeMachine())
        let c = try XCTUnwrap(book.add(lang: "en", word: "water",
                                       snapshot: .object(["senses": .array([.array(["sv", "vatten"])])])))
        try book.setNote(c.id, "say \"hi\", twice")
        let lines = book.csv().components(separatedBy: "\r\n")
        XCTAssertEqual(lines[0], "word,lang,meaning,due,interval_days,reps,lapses,note,added")
        XCTAssertTrue(lines[1].hasPrefix("water,en,vatten,"))
        XCTAssertTrue(lines[1].contains("\"say \"\"hi\"\", twice\""))
    }

    func testTimestampsLookLikeTheDesktops() {
        let ts = Day.timestamp(Date(timeIntervalSince1970: 0))
        XCTAssertNotNil(ts.range(of: #"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}[+-]\d\d:\d\d$"#,
                                 options: .regularExpression), ts)
        XCTAssertEqual(Day.adding(1, to: "2024-02-28"), "2024-02-29")
        XCTAssertEqual(Day.between("2024-02-28", "2024-03-01"), 2)
    }
}
