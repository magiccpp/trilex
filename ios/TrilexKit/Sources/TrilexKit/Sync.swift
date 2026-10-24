// Port of trilex/sync.py: client for the Þríauga sync service.
//
// Sync is entirely optional. The dictionary never touches the network; only
// the wordbook - the words you chose to save and their review schedule - is
// exchanged, and only once you have signed in.
//
// Conflicts resolve last-write-wins on `updated_at`, matching the server. A
// deletion travels as a tombstone so removing a word on one device does not
// get undone by the next device that pushes.
import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

public let DEFAULT_SYNC_URL = "https://xiaodong.io/thriauga"

public struct SyncError: Error, LocalizedError, Equatable {
    public let message: String
    public init(_ m: String) { message = m }
    public var errorDescription: String? { message }
}

/// The one thing sync needs from the network, so tests can stand in a fake
/// server and the app can use URLSession.
public protocol HTTPTransport: Sendable {
    func send(_ request: URLRequest) async throws -> (Data, Int)
}

public struct URLSessionTransport: HTTPTransport {
    public init() {}

    public func send(_ request: URLRequest) async throws -> (Data, Int) {
        try await withCheckedThrowingContinuation { cont in
            URLSession.shared.dataTask(with: request) { data, resp, err in
                if let err {
                    cont.resume(throwing: err)
                } else {
                    cont.resume(returning: (data ?? Data(), (resp as? HTTPURLResponse)?.statusCode ?? 0))
                }
            }.resume()
        }
    }
}

public struct SyncResult: Equatable, Sendable {
    public var pushed = 0
    public var pulled = 0
    public var applied = 0
    public var skipped = 0
    public var rev = 0

    public var summary: String {
        if pushed == 0 && pulled == 0 { return "Already up to date" }
        var bits: [String] = []
        if pushed > 0 { bits.append("sent \(pushed)") }
        if applied > 0 { bits.append("received \(applied)") }
        return bits.isEmpty ? "Up to date" : bits.joined(separator: ", ")
    }
}

/// Talks to the server and reconciles it with the local wordbook.
public final class SyncClient: @unchecked Sendable {
    public let book: Wordbook
    public var baseURL: String
    let transport: HTTPTransport
    let deviceName: String
    static let timeout: TimeInterval = 25

    private var db: SQLiteDB { book.db }

    public init(book: Wordbook, baseURL: String? = nil, transport: HTTPTransport = URLSessionTransport(),
                deviceName: String = "iOS") {
        self.book = book
        self.transport = transport
        self.deviceName = String(deviceName.prefix(80))
        self.baseURL = ""
        let stored = state("base_url")
        self.baseURL = (baseURL ?? stored ?? DEFAULT_SYNC_URL).trimmingTrailingSlashes()
    }

    // ------------------------------------------------------------ persistence
    public func state(_ k: String) -> String? {
        (try? db.first("SELECT v FROM sync_state WHERE k=?", [.text(k)]))?.string("v")
    }

    public func setState(_ k: String, _ v: String) {
        _ = try? db.run("INSERT OR REPLACE INTO sync_state(k,v) VALUES(?,?)", [.text(k), .text(v)])
    }

    public var token: String? { state("token") }
    public var email: String? { state("email") }
    public var signedIn: Bool { !(token ?? "").isEmpty }
    public var lastSync: String? { state("last_sync") }

    public func signOut() {
        for k in ["token", "email", "rev"] {
            _ = try? db.run("DELETE FROM sync_state WHERE k=?", [.text(k)])
        }
    }

    // ------------------------------------------------------------------ HTTP
    func call(_ method: String, _ path: String, body: Any? = nil, auth: Bool = true) async throws -> [String: Any] {
        guard let url = URL(string: baseURL + path) else { throw SyncError("Bad server address") }
        var req = URLRequest(url: url, timeoutInterval: Self.timeout)
        req.httpMethod = method
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        if let body { req.httpBody = try JSONSerialization.data(withJSONObject: body) }
        if auth {
            guard let t = token, !t.isEmpty else { throw SyncError("Not signed in") }
            req.setValue("Bearer \(t)", forHTTPHeaderField: "Authorization")
        }
        let data: Data, status: Int
        do {
            (data, status) = try await transport.send(req)
        } catch let e as URLError where e.code == .timedOut {
            throw SyncError("Server timed out")
        } catch {
            throw SyncError("Cannot reach \(baseURL) (\(error.localizedDescription))")
        }
        let obj = data.isEmpty ? nil : (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        guard (200..<300).contains(status) else {
            if status == 401 { throw SyncError("Session expired - sign in again") }
            if status == 429 { throw SyncError("Too many attempts. Wait a few minutes.") }
            if let d = obj?["detail"] as? String, !d.isEmpty { throw SyncError(d) }
            throw SyncError("Server error \(status)")
        }
        return obj ?? [:]
    }

    // ------------------------------------------------------------------ auth
    public func requestCode(email: String) async throws {
        _ = try await call("POST", "/v1/auth/request", body: ["email": email], auth: false)
    }

    public func verify(email: String, code: String) async throws {
        let r = try await call("POST", "/v1/auth/verify",
                               body: ["email": email, "code": code, "device": deviceName], auth: false)
        guard let t = r["token"] as? String, !t.isEmpty else {
            throw SyncError("Server did not return a token")
        }
        setState("token", t)
        setState("email", (r["email"] as? String) ?? email)
        setState("base_url", baseURL)
    }

    public func me() async throws -> [String: Any] {
        try await call("GET", "/v1/me")
    }

    // ------------------------------------------------------------------ sync
    func localChanges() -> [[String: Any]] {
        let rows = (try? db.query("SELECT * FROM card WHERE uid IS NOT NULL AND dirty=1")) ?? []
        return rows.map { r in
            func s(_ k: String) -> Any { r.string(k) ?? NSNull() }
            var snap: Any = NSNull()
            if let t = r.string("snapshot"), let d = t.data(using: .utf8),
               let o = try? JSONSerialization.jsonObject(with: d, options: [.fragmentsAllowed]) {
                snap = o
            }
            return [
                "uid": s("uid"), "lang": s("lang"), "word": s("word"),
                "note": r.string("note") ?? "", "added_at": s("added_at"), "due": s("due"),
                "stability": r.double("stability") ?? 0.0, "difficulty": r.double("difficulty") ?? 0.0,
                "reps": r.int("reps") ?? 0, "lapses": r.int("lapses") ?? 0,
                "last_review": s("last_review"),
                "updated_at": r.string("updated_at") ?? book.nowISO(),
                "snapshot": snap, "deleted": (r.int("deleted") ?? 0) != 0,
            ]
        }
    }

    /// Merge server cards into the local wordbook, last-write-wins.
    func apply(_ cards: [[String: Any]]) throws -> Int {
        var applied = 0
        try db.transaction {
            for c in cards {
                guard let uid = c["uid"] as? String, let lang = c["lang"] as? String,
                      let word = c["word"] as? String else { continue }
                let updated = (c["updated_at"] as? String) ?? ""
                let local = try db.first("SELECT id, updated_at FROM card WHERE uid=?", [.text(uid)])
                if let local, (local.string("updated_at") ?? "") >= updated { continue }
                var snap: SQLValue = .null
                if let o = c["snapshot"], !(o is NSNull),
                   let d = try? JSONSerialization.data(withJSONObject: o, options: [.fragmentsAllowed]) {
                    snap = .text(String(decoding: d, as: UTF8.self))
                }
                func str(_ k: String) -> SQLValue { .of(c[k] as? String) }
                func num(_ k: String) -> Double { jsonNumber(c[k]) ?? 0 }
                let vals: [SQLValue] = [
                    .text(lang), .text(word), .text((c["note"] as? String) ?? ""), snap,
                    .text((c["added_at"] as? String) ?? book.today),
                    .text((c["due"] as? String) ?? book.today),
                    .of(num("stability")), .of(num("difficulty")),
                    .of(Int(num("reps"))), .of(Int(num("lapses"))),
                    str("last_review"), .of(jsonBool(c["deleted"]) ? 1 : 0),
                    .text(updated), .text(uid),
                ]
                if local != nil {
                    try db.run("UPDATE card SET lang=?,word=?,note=?,snapshot=?,added_at=?,"
                               + "due=?,stability=?,difficulty=?,reps=?,lapses=?,last_review=?,"
                               + "deleted=?,updated_at=?,dirty=0 WHERE uid=?", vals)
                } else {
                    // A word saved on another device may collide by (lang, word)
                    // here; the UNIQUE constraint would reject it, so drop the
                    // older local duplicate and let the synced one win.
                    try db.run("DELETE FROM card WHERE lang=? AND word=? AND uid<>?",
                               [.text(lang), .text(word), .text(uid)])
                    try db.run("INSERT INTO card(lang,word,note,snapshot,added_at,due,"
                               + "stability,difficulty,reps,lapses,last_review,deleted,"
                               + "updated_at,uid,dirty) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)", vals)
                }
                applied += 1
            }
        }
        return applied
    }

    public func sync() async throws -> SyncResult {
        let sinceRev = Int(state("rev") ?? "0") ?? 0
        let outgoing = localChanges()
        let pushedUids = outgoing.compactMap { $0["uid"] as? String }
        var res = SyncResult(pushed: outgoing.count)
        let r = try await call("POST", "/v1/sync", body: ["since": sinceRev, "cards": outgoing])
        let incoming = (r["cards"] as? [[String: Any]]) ?? []
        res.pulled = incoming.count
        res.applied = try apply(incoming)
        res.skipped = jsonNumber(r["skipped"]).map { Int($0) } ?? 0
        res.rev = jsonNumber(r["rev"]).map { Int($0) } ?? sinceRev
        // Clear the flag only now that the server has acknowledged the push.
        if !pushedUids.isEmpty {
            try db.transaction {
                for u in pushedUids { try db.run("UPDATE card SET dirty=0 WHERE uid=?", [.text(u)]) }
            }
        }
        setState("rev", String(res.rev))
        let ts = book.nowISO()
        let stamp = String(ts.prefix(19)) + String(ts.suffix(6))
        setState("last_push", stamp)
        setState("last_sync", stamp)
        return res
    }
}

// JSONSerialization hands back NSNumber on Apple platforms and native Swift
// numbers on Linux; accept either.
func jsonNumber(_ v: Any?) -> Double? {
    switch v {
    case let d as Double: return d
    case let i as Int: return Double(i)
    case let n as NSNumber: return n.doubleValue
    default: return nil
    }
}

func jsonBool(_ v: Any?) -> Bool {
    switch v {
    case let b as Bool: return b
    case let n as NSNumber: return n.boolValue
    default: return false
    }
}

extension String {
    func trimmingTrailingSlashes() -> String {
        var s = self
        while s.hasSuffix("/") { s.removeLast() }
        return s
    }
}
