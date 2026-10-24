// A deliberately small SQLite wrapper: prepare, bind, step, read by column
// name. The dictionary is plain SQLite with no extensions, so the C API is
// all the iOS port needs - no third-party database library.
import Foundation
#if canImport(SQLite3)
import SQLite3
#else
import CSQLite
#endif

public enum SQLValue: Equatable, Sendable {
    case null
    case int(Int64)
    case double(Double)
    case text(String)
    case blob(Data)
}

extension SQLValue: ExpressibleByStringLiteral, ExpressibleByIntegerLiteral,
                    ExpressibleByFloatLiteral, ExpressibleByNilLiteral {
    public init(stringLiteral v: String) { self = .text(v) }
    public init(integerLiteral v: Int) { self = .int(Int64(v)) }
    public init(floatLiteral v: Double) { self = .double(v) }
    public init(nilLiteral: ()) { self = .null }
}

public extension SQLValue {
    static func of(_ s: String?) -> SQLValue { s.map { .text($0) } ?? .null }
    static func of(_ i: Int) -> SQLValue { .int(Int64(i)) }
    static func of(_ d: Double) -> SQLValue { .double(d) }
}

public struct SQLiteError: Error, CustomStringConvertible {
    public let code: Int32
    public let message: String
    public var description: String { "SQLite error \(code): \(message)" }
}

public struct Row {
    let names: [String: Int]
    let values: [SQLValue]

    public subscript(_ name: String) -> SQLValue {
        guard let i = names[name] else { return .null }
        return values[i]
    }

    public func string(_ name: String) -> String? {
        switch self[name] {
        case .text(let s): return s
        case .int(let i): return String(i)
        case .double(let d): return String(d)
        default: return nil
        }
    }

    public func int(_ name: String) -> Int? {
        switch self[name] {
        case .int(let i): return Int(i)
        case .double(let d): return Int(d)
        case .text(let s): return Int(s)
        default: return nil
        }
    }

    public func double(_ name: String) -> Double? {
        switch self[name] {
        case .int(let i): return Double(i)
        case .double(let d): return d
        case .text(let s): return Double(s)
        default: return nil
        }
    }

    public func data(_ name: String) -> Data? {
        switch self[name] {
        case .blob(let d): return d
        case .text(let s): return Data(s.utf8)
        default: return nil
        }
    }

    public func isNull(_ name: String) -> Bool { self[name] == .null }
}

// SQLITE_TRANSIENT is a macro the importer cannot see.
private let TRANSIENT = unsafeBitCast(-1, to: sqlite3_destructor_type.self)

/// One connection, safe to share across threads. SQLite's serialized mode
/// protects single statements; `lock` also keeps a transaction whole, so a
/// background sync and a review on the main thread cannot interleave.
public final class SQLiteDB: @unchecked Sendable {
    let handle: OpaquePointer
    public let path: String
    private let lock = NSRecursiveLock()

    public init(path: String, readOnly: Bool = false) throws {
        var h: OpaquePointer?
        let flags = (readOnly ? SQLITE_OPEN_READONLY : (SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE))
            | SQLITE_OPEN_FULLMUTEX
        let rc = sqlite3_open_v2(path, &h, flags, nil)
        guard rc == SQLITE_OK, let h else {
            let msg = h.map { String(cString: sqlite3_errmsg($0)) } ?? "cannot open"
            if let h { sqlite3_close(h) }
            throw SQLiteError(code: rc, message: "\(msg) (\(path))")
        }
        self.handle = h
        self.path = path
        sqlite3_busy_timeout(h, 30000)
    }

    deinit { sqlite3_close_v2(handle) }

    private func error(_ rc: Int32) -> SQLiteError {
        SQLiteError(code: rc, message: String(cString: sqlite3_errmsg(handle)))
    }

    /// Run one or more statements that take no parameters (schema scripts).
    public func execute(_ sql: String) throws {
        lock.lock()
        defer { lock.unlock() }
        var err: UnsafeMutablePointer<CChar>?
        let rc = sqlite3_exec(handle, sql, nil, nil, &err)
        if rc != SQLITE_OK {
            let msg = err.map { String(cString: $0) } ?? "exec failed"
            sqlite3_free(err)
            throw SQLiteError(code: rc, message: msg)
        }
    }

    private func prepare(_ sql: String, _ args: [SQLValue]) throws -> OpaquePointer {
        var st: OpaquePointer?
        let rc = sqlite3_prepare_v2(handle, sql, -1, &st, nil)
        guard rc == SQLITE_OK, let st else { throw error(rc) }
        for (i, a) in args.enumerated() {
            let idx = Int32(i + 1)
            let r: Int32
            switch a {
            case .null: r = sqlite3_bind_null(st, idx)
            case .int(let v): r = sqlite3_bind_int64(st, idx, v)
            case .double(let v): r = sqlite3_bind_double(st, idx, v)
            case .text(let v): r = sqlite3_bind_text(st, idx, v, -1, TRANSIENT)
            case .blob(let v):
                r = v.withUnsafeBytes { p in
                    sqlite3_bind_blob(st, idx, p.baseAddress, Int32(v.count), TRANSIENT)
                }
            }
            if r != SQLITE_OK {
                sqlite3_finalize(st)
                throw error(r)
            }
        }
        return st
    }

    /// Every row a query returns.
    public func query(_ sql: String, _ args: [SQLValue] = []) throws -> [Row] {
        lock.lock()
        defer { lock.unlock() }
        let st = try prepare(sql, args)
        defer { sqlite3_finalize(st) }
        let n = sqlite3_column_count(st)
        var names: [String: Int] = [:]
        for i in 0..<n {
            let name = String(cString: sqlite3_column_name(st, i))
            if names[name] == nil { names[name] = Int(i) }
        }
        var out: [Row] = []
        while true {
            let rc = sqlite3_step(st)
            if rc == SQLITE_DONE { break }
            guard rc == SQLITE_ROW else { throw error(rc) }
            var vals: [SQLValue] = []
            vals.reserveCapacity(Int(n))
            for i in 0..<n {
                switch sqlite3_column_type(st, i) {
                case SQLITE_INTEGER: vals.append(.int(sqlite3_column_int64(st, i)))
                case SQLITE_FLOAT: vals.append(.double(sqlite3_column_double(st, i)))
                case SQLITE_TEXT:
                    vals.append(.text(String(cString: sqlite3_column_text(st, i))))
                case SQLITE_BLOB:
                    let len = Int(sqlite3_column_bytes(st, i))
                    if let p = sqlite3_column_blob(st, i), len > 0 {
                        vals.append(.blob(Data(bytes: p, count: len)))
                    } else {
                        vals.append(.blob(Data()))
                    }
                default: vals.append(.null)
                }
            }
            out.append(Row(names: names, values: vals))
        }
        return out
    }

    public func first(_ sql: String, _ args: [SQLValue] = []) throws -> Row? {
        try query(sql, args).first
    }

    /// A statement run for its effect (INSERT, UPDATE, DELETE, ATTACH...).
    @discardableResult
    public func run(_ sql: String, _ args: [SQLValue] = []) throws -> Int {
        lock.lock()
        defer { lock.unlock() }
        let st = try prepare(sql, args)
        defer { sqlite3_finalize(st) }
        while true {
            let rc = sqlite3_step(st)
            if rc == SQLITE_DONE { break }
            if rc != SQLITE_ROW { throw error(rc) }
        }
        return Int(sqlite3_changes(handle))
    }

    public func transaction<T>(_ body: () throws -> T) throws -> T {
        lock.lock()
        defer { lock.unlock() }
        try execute("BEGIN")
        do {
            let v = try body()
            try execute("COMMIT")
            return v
        } catch {
            try? execute("ROLLBACK")
            throw error
        }
    }

    /// Placeholder list for an IN clause: "?,?,?".
    public static func marks(_ n: Int) -> String {
        Array(repeating: "?", count: n).joined(separator: ",")
    }
}
