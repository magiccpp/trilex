import Foundation
import XCTest
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif
@testable import TrilexKit

/// The sync API of server/api/app/main.py, in memory: last-write-wins on
/// updated_at, a per-user revision counter, tombstones kept.
final class FakeServer: HTTPTransport, @unchecked Sendable {
    private let lock = NSLock()
    var cards: [String: [String: Any]] = [:]
    var rev = 0
    var requests: [String] = []
    let token = "tok-123"

    func send(_ request: URLRequest) async throws -> (Data, Int) {
        handle(request)
    }

    private func handle(_ request: URLRequest) -> (Data, Int) {
        lock.lock()
        defer { lock.unlock() }
        let path = request.url!.path
        requests.append("\(request.httpMethod!) \(path)")
        let body = request.httpBody.flatMap { try? JSONSerialization.jsonObject(with: $0) } as? [String: Any]
        func reply(_ o: Any, _ status: Int = 200) -> (Data, Int) {
            (try! JSONSerialization.data(withJSONObject: o), status)
        }
        switch path {
        case "/thriauga/v1/auth/request":
            return (Data(), 204)
        case "/thriauga/v1/auth/verify":
            guard body?["code"] as? String == "123456" else { return reply(["detail": "Wrong code"], 400) }
            return reply(["token": token, "email": body!["email"]!])
        case "/thriauga/v1/sync":
            guard request.value(forHTTPHeaderField: "Authorization") == "Bearer \(token)" else {
                return reply(["detail": "unauthorised"], 401)
            }
            var applied = 0, skipped = 0
            for c in (body?["cards"] as? [[String: Any]]) ?? [] {
                let uid = c["uid"] as! String
                if let ex = cards[uid],
                   iso(ex["updated_at"] as! String) >= iso(c["updated_at"] as! String) {
                    skipped += 1
                    continue
                }
                rev += 1
                var stored = c
                stored["rev"] = rev
                cards[uid] = stored
                applied += 1
            }
            let since = jsonNumber(body?["since"]).map { Int($0) } ?? 0
            let out = cards.values.filter { (($0["rev"] as? Int) ?? 0) > since }
                .sorted { ($0["rev"] as! Int) < ($1["rev"] as! Int) }
            return reply(["rev": rev, "applied": applied, "skipped": skipped, "cards": out])
        default:
            return reply(["detail": "not found"], 404)
        }
    }

    /// The server parses timestamps, so offsets compare correctly.
    private func iso(_ s: String) -> Date {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f.date(from: s) ?? ISO8601DateFormatter().date(from: s) ?? .distantPast
    }
}

final class SyncTests: XCTestCase {
    let base = "https://example.test/thriauga"

    func device(_ server: FakeServer, _ clock: TimeMachine) throws -> SyncClient {
        let book = try Wordbook(db: SQLiteDB(path: ":memory:"), now: { clock.now })
        return SyncClient(book: book, baseURL: base, transport: server, deviceName: "test")
    }

    func signIn(_ c: SyncClient) async throws {
        try await c.requestCode(email: "me@example.test")
        try await c.verify(email: "me@example.test", code: "123456")
    }

    func testTwoDevicesConverge() async throws {
        let server = FakeServer()
        let clock = TimeMachine()
        let phone = try device(server, clock), laptop = try device(server, clock)
        try await signIn(phone)
        try await signIn(laptop)
        XCTAssertTrue(phone.signedIn)
        XCTAssertEqual(phone.email, "me@example.test")

        let card = try XCTUnwrap(phone.book.add(lang: "sv", word: "hus",
                                                snapshot: .object(["senses": .array([.array(["en", "house"])])])))
        var r = try await phone.sync()
        XCTAssertEqual(r.pushed, 1)
        XCTAssertEqual(r.summary, "sent 1")

        r = try await laptop.sync()
        XCTAssertEqual(r.applied, 1)
        let onLaptop = try XCTUnwrap(laptop.book.get(lang: "sv", word: "hus"))
        XCTAssertEqual(onLaptop.uid, card.uid)
        XCTAssertEqual(onLaptop.meaning(), "house")

        // Review on the laptop, later; the phone picks up the new schedule.
        clock.travel(days: 1)
        try laptop.book.grade(onLaptop, .good)
        _ = try await laptop.sync()
        r = try await phone.sync()
        XCTAssertEqual(r.applied, 1)
        XCTAssertEqual(try phone.book.get(lang: "sv", word: "hus")?.reps, 1)

        // Delete on the phone; the tombstone reaches the laptop.
        clock.travel(days: 1)
        try phone.book.remove(card.id)
        _ = try await phone.sync()
        _ = try await laptop.sync()
        XCTAssertFalse(laptop.book.has(lang: "sv", word: "hus"))

        // Nothing left to say.
        r = try await phone.sync()
        XCTAssertEqual(r.pushed, 0)
        XCTAssertEqual(r.summary, "Already up to date")
    }

    func testSameWordSavedOnBothDevicesKeepsOneCard() async throws {
        let server = FakeServer()
        let clock = TimeMachine()
        let phone = try device(server, clock), laptop = try device(server, clock)
        try await signIn(phone)
        try await signIn(laptop)
        try phone.book.add(lang: "en", word: "water")
        clock.travel(days: 1)
        try laptop.book.add(lang: "en", word: "water")
        _ = try await phone.sync()
        _ = try await laptop.sync()
        _ = try await phone.sync()
        XCTAssertEqual(phone.book.all().count, 1)
        XCTAssertEqual(laptop.book.all().count, 1)
    }

    func testErrorsAreReadable() async throws {
        let server = FakeServer()
        let c = try device(server, TimeMachine())
        do {
            _ = try await c.sync()
            XCTFail("sync without a token should fail")
        } catch let e as SyncError {
            XCTAssertEqual(e.message, "Not signed in")
        }
        do {
            try await c.verify(email: "me@example.test", code: "000000")
            XCTFail("a wrong code should fail")
        } catch let e as SyncError {
            XCTAssertEqual(e.message, "Wrong code")
        }
        c.setState("token", "stale")
        do {
            _ = try await c.sync()
            XCTFail("a stale token should fail")
        } catch let e as SyncError {
            XCTAssertEqual(e.message, "Session expired - sign in again")
        }
        c.signOut()
        XCTAssertFalse(c.signedIn)
    }
}
