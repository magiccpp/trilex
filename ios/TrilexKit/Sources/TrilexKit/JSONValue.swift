// Free-form JSON: entry `extra` columns and wordbook snapshots have no fixed
// shape, and the sync server stores a snapshot verbatim.
import Foundation

public enum JSONValue: Codable, Equatable, Sendable {
    case null
    case bool(Bool)
    case number(Double)
    case string(String)
    case array([JSONValue])
    case object([String: JSONValue])

    public init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let b = try? c.decode(Bool.self) { self = .bool(b) }
        else if let d = try? c.decode(Double.self) { self = .number(d) }
        else if let s = try? c.decode(String.self) { self = .string(s) }
        else if let a = try? c.decode([JSONValue].self) { self = .array(a) }
        else { self = .object(try c.decode([String: JSONValue].self)) }
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .null: try c.encodeNil()
        case .bool(let b): try c.encode(b)
        case .number(let d):
            if d.rounded() == d && abs(d) < 1e15 { try c.encode(Int64(d)) } else { try c.encode(d) }
        case .string(let s): try c.encode(s)
        case .array(let a): try c.encode(a)
        case .object(let o): try c.encode(o)
        }
    }

    public subscript(_ key: String) -> JSONValue? {
        if case .object(let o) = self { return o[key] }
        return nil
    }

    public var string: String? {
        if case .string(let s) = self { return s }
        return nil
    }

    public var array: [JSONValue]? {
        if case .array(let a) = self { return a }
        return nil
    }

    /// Python truthiness of a string value: missing, null and "" are all false.
    public var nonEmptyString: String? {
        guard let s = string, !s.isEmpty else { return nil }
        return s
    }

    public static func parse(_ text: String?) -> JSONValue? {
        guard let text, let data = text.data(using: .utf8) else { return nil }
        return try? JSONDecoder().decode(JSONValue.self, from: data)
    }

    public func serialized() -> String {
        let enc = JSONEncoder()
        enc.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        guard let d = try? enc.encode(self) else { return "null" }
        return String(decoding: d, as: UTF8.self)
    }
}

extension JSONValue: ExpressibleByStringLiteral {
    public init(stringLiteral v: String) { self = .string(v) }
}

public extension JSONValue {
    static func of(_ s: String?) -> JSONValue { s.map { .string($0) } ?? .null }
}
