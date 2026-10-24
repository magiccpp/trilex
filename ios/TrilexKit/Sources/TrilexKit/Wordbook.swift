// Port of trilex/srs.py and the user-database half of trilex/db.py.
//
// user.db (your words) is deliberately a separate file from dict.db (the
// generated dictionary), so replacing the dictionary can never touch your
// wordbook. The schema is identical to the desktop's, so the same sync
// server reconciles both.
//
// Deletions are tombstones rather than row removals, so that removing a word
// on one device propagates through sync instead of being resurrected by the
// next device that pushes.
import Foundation

let USER_SCHEMA = """
CREATE TABLE IF NOT EXISTS card(
  id        INTEGER PRIMARY KEY,
  lang      TEXT NOT NULL,
  word      TEXT NOT NULL,
  note      TEXT DEFAULT '',
  snapshot  TEXT,
  added_at  TEXT NOT NULL,
  due       TEXT NOT NULL,
  interval  REAL NOT NULL DEFAULT 0,
  ease      REAL NOT NULL DEFAULT 2.5,
  reps      INTEGER NOT NULL DEFAULT 0,
  lapses    INTEGER NOT NULL DEFAULT 0,
  stability   REAL NOT NULL DEFAULT 0,
  difficulty  REAL NOT NULL DEFAULT 0,
  last_review TEXT,
  uid        TEXT,
  updated_at TEXT,
  deleted    INTEGER NOT NULL DEFAULT 0,
  dirty      INTEGER NOT NULL DEFAULT 1,
  UNIQUE(lang, word)
);
CREATE TABLE IF NOT EXISTS review(
  id      INTEGER PRIMARY KEY,
  card_id INTEGER NOT NULL REFERENCES card(id) ON DELETE CASCADE,
  ts      TEXT NOT NULL,
  grade   INTEGER NOT NULL,
  interval REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_card_due ON card(due);
CREATE TABLE IF NOT EXISTS sync_state(
  k TEXT PRIMARY KEY, v TEXT
);
CREATE INDEX IF NOT EXISTS ix_rev_card ON review(card_id);
"""

/// Calendar days as the desktop stores them: local-time "YYYY-MM-DD".
public enum Day {
    static var calendar: Calendar {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = .current
        return c
    }

    public static func string(_ d: Date) -> String {
        let c = calendar.dateComponents([.year, .month, .day], from: d)
        return String(format: "%04d-%02d-%02d", c.year!, c.month!, c.day!)
    }

    public static func date(_ s: String) -> Date? {
        let p = s.prefix(10).split(separator: "-").compactMap { Int($0) }
        guard p.count == 3 else { return nil }
        return calendar.date(from: DateComponents(year: p[0], month: p[1], day: p[2]))
    }

    public static func between(_ a: String, _ b: String) -> Int? {
        guard let da = date(a), let db = date(b) else { return nil }
        return calendar.dateComponents([.day], from: da, to: db).day
    }

    public static func adding(_ n: Int, to s: String) -> String {
        guard let d = date(s), let r = calendar.date(byAdding: .day, value: n, to: d) else { return s }
        return string(r)
    }

    /// Local time with offset and milliseconds, like Python's
    /// `datetime.now().astimezone().isoformat(timespec="milliseconds")`.
    public static func timestamp(_ d: Date) -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.calendar = Calendar(identifier: .gregorian)
        f.timeZone = .current
        f.dateFormat = "yyyy-MM-dd'T'HH:mm:ss.SSSxxx"
        return f.string(from: d)
    }
}

public struct Card: Identifiable, Equatable, Sendable {
    public let id: Int
    public let lang: String
    public let word: String
    public let note: String
    public let snapshot: JSONValue?
    public let addedAt: String
    public let due: String
    public let interval: Double
    public let stability: Double
    public let difficulty: Double
    public let reps: Int
    public let lapses: Int
    public let lastReview: String?
    public let uid: String
    public let updatedAt: String
    public let deleted: Bool

    init(_ r: Row) {
        id = r.int("id")!
        lang = r.string("lang")!
        word = r.string("word")!
        note = r.string("note") ?? ""
        snapshot = JSONValue.parse(r.string("snapshot"))
        addedAt = r.string("added_at") ?? ""
        due = r.string("due") ?? ""
        interval = r.double("interval") ?? 0
        stability = r.double("stability") ?? 0
        difficulty = r.double("difficulty") ?? 0
        reps = r.int("reps") ?? 0
        lapses = r.int("lapses") ?? 0
        lastReview = r.string("last_review")
        uid = r.string("uid") ?? ""
        updatedAt = r.string("updated_at") ?? ""
        deleted = (r.int("deleted") ?? 0) != 0
    }

    public var memory: Memory {
        Memory(stability: stability, difficulty: difficulty, reps: reps, lapses: lapses)
    }

    public var isNew: Bool { reps == 0 || stability <= 0 }

    public func isDue(today: String) -> Bool { String(due.prefix(10)) <= today }

    public func elapsedDays(today: String) -> Double {
        guard let lr = lastReview, let d = Day.between(String(lr.prefix(10)), today) else { return 0 }
        return Double(max(0, d))
    }

    /// Predicted probability you would recall this word right now.
    public func retention(today: String) -> Double {
        isNew ? 1.0 : Forgetting.retrievability(elapsedDays(today: today), stability)
    }

    /// The first few glosses recorded when the word was saved.
    public func meaning(_ n: Int = 2) -> String {
        let senses = snapshot?["senses"]?.array ?? []
        let gl = senses.prefix(n).compactMap { $0.array?.last?.string }
        return gl.isEmpty ? "" : gl.joined(separator: "; ")
    }
}

public struct WordbookCounts: Equatable, Sendable {
    public let total: Int
    public let due: Int
    public let new: Int
    public let review: Int
}

public enum CardOrder: String, CaseIterable, Sendable {
    case due, added, word, lang

    var sql: String {
        switch self {
        case .due: return "due, added_at"
        case .added: return "added_at DESC"
        case .word: return "word"
        case .lang: return "lang, word"
        }
    }
}

public final class Wordbook: @unchecked Sendable {
    public let db: SQLiteDB
    public var retentionTarget: Double
    /// Injectable so tests can travel in time.
    public var now: () -> Date

    // Columns added after the first desktop release; SQLite has no ADD COLUMN
    // IF NOT EXISTS, so widen an existing wordbook by inspecting the table.
    static let migrations: [(String, String)] = [
        ("stability", "REAL NOT NULL DEFAULT 0"),
        ("difficulty", "REAL NOT NULL DEFAULT 0"),
        ("last_review", "TEXT"),
        ("uid", "TEXT"),
        ("updated_at", "TEXT"),
        ("deleted", "INTEGER NOT NULL DEFAULT 0"),
        ("dirty", "INTEGER NOT NULL DEFAULT 1"),
    ]

    public init(db: SQLiteDB, retentionTarget: Double = Forgetting.DEFAULT_RETENTION,
                now: @escaping () -> Date = Date.init) throws {
        self.db = db
        self.retentionTarget = retentionTarget
        self.now = now
        try db.execute("PRAGMA foreign_keys=ON; PRAGMA journal_mode=WAL;")
        try db.execute(USER_SCHEMA)
        let have = Set(try db.query("PRAGMA table_info(card)").compactMap { $0.string("name") })
        for (col, decl) in Self.migrations where !have.contains(col) {
            try db.execute("ALTER TABLE card ADD COLUMN \(col) \(decl)")
        }
        // After the migrations, not in the schema script: on a wordbook from
        // before sync existed the uid column does not exist yet.
        try db.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_card_uid ON card(uid)")
        // Backfill sync identity for rows saved before sync existed.
        for r in try db.query("SELECT id FROM card WHERE uid IS NULL") {
            try db.run("UPDATE card SET uid=?, updated_at=COALESCE(updated_at,?) WHERE id=?",
                       [.text(UUID().uuidString.lowercased()), .text(nowISO()), .of(r.int("id")!)])
        }
    }

    public convenience init(path: String) throws {
        try self.init(db: SQLiteDB(path: path))
    }

    public var today: String { Day.string(now()) }
    func nowISO() -> String { Day.timestamp(now()) }

    // ------------------------------------------------------------------ CRUD
    @discardableResult
    public func add(lang: String, word: String, snapshot: JSONValue? = nil, note: String = "") throws -> Card? {
        if let existing = try db.first("SELECT id FROM card WHERE lang=? AND word=?",
                                       [.text(lang), .text(word)]) {
            // Re-adding a tombstoned word revives it rather than duplicating.
            try db.run("UPDATE card SET deleted=0, updated_at=?, dirty=1 WHERE id=?",
                       [.text(nowISO()), .of(existing.int("id")!)])
        } else {
            try db.run("INSERT INTO card(lang,word,note,snapshot,added_at,due,uid,updated_at,dirty) "
                       + "VALUES(?,?,?,?,?,?,?,?,1)",
                       [.text(lang), .text(word), .text(note), .of(snapshot?.serialized()),
                        .text(nowISO()), .text(today), .text(UUID().uuidString.lowercased()),
                        .text(nowISO())])
        }
        return try get(lang: lang, word: word)
    }

    public func get(lang: String, word: String) throws -> Card? {
        try db.first("SELECT * FROM card WHERE lang=? AND word=?", [.text(lang), .text(word)]).map(Card.init)
    }

    public func byUid(_ uid: String) throws -> Card? {
        try db.first("SELECT * FROM card WHERE uid=?", [.text(uid)]).map(Card.init)
    }

    public func has(lang: String, word: String) -> Bool {
        (try? db.first("SELECT 1 FROM card WHERE lang=? AND word=? AND deleted=0",
                       [.text(lang), .text(word)])) != nil
    }

    /// Tombstone, so the deletion survives a sync round trip.
    public func remove(_ cardId: Int) throws {
        try db.run("UPDATE card SET deleted=1, updated_at=?, dirty=1 WHERE id=?",
                   [.text(nowISO()), .of(cardId)])
    }

    public func purgeDeleted(olderThanDays: Int = 60) throws {
        try db.run("DELETE FROM card WHERE deleted=1 AND updated_at < ?",
                   [.text(Day.adding(-olderThanDays, to: today))])
    }

    public func setNote(_ cardId: Int, _ note: String) throws {
        try db.run("UPDATE card SET note=?, updated_at=?, dirty=1 WHERE id=?",
                   [.text(note), .text(nowISO()), .of(cardId)])
    }

    public func all(order: CardOrder = .due) -> [Card] {
        ((try? db.query("SELECT * FROM card WHERE deleted=0 ORDER BY \(order.sql)")) ?? []).map(Card.init)
    }

    // ------------------------------------------------------------- scheduling
    /// Cards to review, most-forgotten first.
    ///
    /// Ordering by predicted retention rather than by due date means that if
    /// you skip a few days, the words you are actually losing surface first
    /// instead of whatever happened to be scheduled earliest.
    public func due(limit: Int? = nil) -> [Card] {
        let t = today
        let cards = ((try? db.query("SELECT * FROM card WHERE deleted=0 AND due<=? ", [.text(t)])) ?? [])
            .map(Card.init)
            .stableSorted { a, b in
                if a.isNew != b.isNew { return a.isNew }
                let ra = a.retention(today: t), rb = b.retention(today: t)
                if ra != rb { return ra < rb }
                return a.due < b.due
            }
        return limit.map { Array(cards.prefix($0)) } ?? cards
    }

    public func counts() -> WordbookCounts {
        let t = today
        let r = try? db.first("SELECT count(*) total, sum(due<=?) due, sum(reps=0) new,"
                              + " sum(due<=? AND reps>0) review FROM card WHERE deleted=0",
                              [.text(t), .text(t)])
        return WordbookCounts(total: r?.int("total") ?? 0, due: r?.int("due") ?? 0,
                              new: r?.int("new") ?? 0, review: r?.int("review") ?? 0)
    }

    /// The next day anything falls due, when nothing is due today.
    public func nextDue() -> String? {
        (try? db.first("SELECT min(due) d FROM card WHERE deleted=0 AND due > ?", [.text(today)]))?.string("d")
    }

    /// Interval each grade would schedule, for the button labels.
    public func preview(_ card: Card) -> [Grade: Double] {
        Forgetting.preview(card.memory, elapsedDays: card.elapsedDays(today: today),
                           retention: retentionTarget)
    }

    @discardableResult
    public func grade(_ card: Card, _ g: Grade) throws -> Card? {
        let t = today
        let mem = card.memory.review(g, elapsedDays: card.elapsedDays(today: t))
        let iv = mem.nextInterval(retention: retentionTarget)
        let due = Day.adding(max(1, Int(iv.rounded(.toNearestOrEven))), to: t)
        try db.transaction {
            try db.run("UPDATE card SET stability=?, difficulty=?, reps=?, lapses=?, "
                       + "due=?, interval=?, last_review=?, updated_at=?, dirty=1 WHERE id=?",
                       [.of(mem.stability), .of(mem.difficulty), .of(mem.reps), .of(mem.lapses),
                        .text(due), .of(iv), .text(t), .text(nowISO()), .of(card.id)])
            try db.run("INSERT INTO review(card_id,ts,grade,interval) VALUES(?,?,?,?)",
                       [.of(card.id), .text(nowISO()), .of(g.rawValue), .of(iv)])
        }
        return try byUid(card.uid) ?? get(lang: card.lang, word: card.word)
    }

    public func averageRetention() -> Double {
        let t = today
        let cards = all().filter { !$0.isNew }
        guard !cards.isEmpty else { return 1.0 }
        return cards.map { $0.retention(today: t) }.reduce(0, +) / Double(cards.count)
    }

    // ----------------------------------------------------------------- export
    /// The same columns as the desktop's CSV export.
    public func csv() -> String {
        func field(_ s: String) -> String {
            s.contains(where: { $0 == "," || $0 == "\"" || $0 == "\n" || $0 == "\r" })
                ? "\"" + s.replacingOccurrences(of: "\"", with: "\"\"") + "\"" : s
        }
        var lines = ["word,lang,meaning,due,interval_days,reps,lapses,note,added"]
        for c in all(order: .added) {
            lines.append([c.word, c.lang, c.meaning(3), c.due, String(c.interval),
                          String(c.reps), String(c.lapses), c.note, c.addedAt].map(field)
                .joined(separator: ","))
        }
        return lines.joined(separator: "\r\n") + "\r\n"
    }
}

public extension Result {
    /// What a saved card remembers about the word, so it still renders if
    /// the dictionary is replaced. Same shape as the desktop's snapshot.
    var snapshot: JSONValue? {
        guard let e = entries.first else { return nil }
        var rel: [String: JSONValue] = [:]
        for (lg, rs) in related {
            rel[lg] = .array(rs.prefix(6).map { r in
                .array([.string(r.word), .of(r.reading), .array(r.glosses.prefix(2).map { .string($0) })])
            })
        }
        return .object([
            "word": .string(e.word), "lang": .string(e.lang), "pos": .of(e.pos),
            "reading": .of(e.reading),
            "senses": .array(e.senses.prefix(8).map { .array([.string($0.lang), .string($0.gloss)]) }),
            "related": .object(rel),
            "etymology": .array(etymologies.filter { !($0.text ?? "").isEmpty }.prefix(3).map {
                .array([.string($0.lang), .string($0.word), .of($0.text)])
            }),
        ])
    }
}
