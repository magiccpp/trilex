// Incremental SHA-256 for verifying downloads. CryptoKit where it exists;
// a portable implementation elsewhere so the package still builds and tests
// on Linux.
import Foundation
#if canImport(CryptoKit)
import CryptoKit
#endif

public struct Digest256 {
    #if canImport(CryptoKit)
    private var h = CryptoKit.SHA256()
    public init() {}
    public mutating func update(_ d: Data) { h.update(data: d) }
    public func hex() -> String { h.finalize().map { String(format: "%02x", $0) }.joined() }
    #else
    private var h = PortableSHA256()
    public init() {}
    public mutating func update(_ d: Data) { h.update(d) }
    public func hex() -> String { h.hex() }
    #endif

    public static func file(_ url: URL) throws -> String {
        let fh = try FileHandle(forReadingFrom: url)
        defer { try? fh.close() }
        var d = Digest256()
        while true {
            let chunk = fh.readData(ofLength: 1 << 20)
            if chunk.isEmpty { break }
            d.update(chunk)
        }
        return d.hex()
    }
}

struct PortableSHA256 {
    private static let k: [UInt32] = [
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
        0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
        0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
        0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
        0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
        0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
        0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
        0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
    ]
    private var state: [UInt32] = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
                                   0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]
    private var buffer: [UInt8] = []
    private var length: UInt64 = 0

    mutating func update(_ d: Data) {
        length += UInt64(d.count)
        buffer.append(contentsOf: d)
        var off = 0
        while buffer.count - off >= 64 {
            block(buffer, off)
            off += 64
        }
        if off > 0 { buffer.removeFirst(off) }
    }

    private mutating func block(_ b: [UInt8], _ o: Int) {
        @inline(__always) func rotr(_ x: UInt32, _ n: UInt32) -> UInt32 { (x >> n) | (x << (32 - n)) }
        var w = [UInt32](repeating: 0, count: 64)
        for i in 0..<16 {
            w[i] = UInt32(b[o + 4 * i]) << 24 | UInt32(b[o + 4 * i + 1]) << 16
                | UInt32(b[o + 4 * i + 2]) << 8 | UInt32(b[o + 4 * i + 3])
        }
        for i in 16..<64 {
            let s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >> 3)
            let s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >> 10)
            w[i] = w[i - 16] &+ s0 &+ w[i - 7] &+ s1
        }
        var a = state[0], bb = state[1], c = state[2], d = state[3]
        var e = state[4], f = state[5], g = state[6], h = state[7]
        for i in 0..<64 {
            let s1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)
            let ch = (e & f) ^ (~e & g)
            let t1 = h &+ s1 &+ ch &+ Self.k[i] &+ w[i]
            let s0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)
            let maj = (a & bb) ^ (a & c) ^ (bb & c)
            let t2 = s0 &+ maj
            h = g; g = f; f = e; e = d &+ t1
            d = c; c = bb; bb = a; a = t1 &+ t2
        }
        state[0] &+= a; state[1] &+= bb; state[2] &+= c; state[3] &+= d
        state[4] &+= e; state[5] &+= f; state[6] &+= g; state[7] &+= h
    }

    func hex() -> String {
        var copy = self
        var tail: [UInt8] = [0x80]
        let rem = Int((length + 1) % 64)
        tail += [UInt8](repeating: 0, count: rem <= 56 ? 56 - rem : 120 - rem)
        let bits = length &* 8
        for i in (0..<8).reversed() { tail.append(UInt8((bits >> (UInt64(i) * 8)) & 0xff)) }
        let savedLength = copy.length
        copy.update(Data(tail))
        copy.length = savedLength
        return copy.state.map { String(format: "%08x", $0) }.joined()
    }
}
