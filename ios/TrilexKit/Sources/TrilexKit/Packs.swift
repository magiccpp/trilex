// Port of trilex/packs.py: the dictionary and its optional media packs.
//
// Everything is published as GitHub release assets, described by one
// manifest.json. A pack is installed simply by placing its file next to
// dict.db - the dictionary ATTACHes whatever it finds.
//
// Pack downloads resume: a partial file is kept as .part and continued with
// an HTTP Range request, which matters for an 80 MB audio pack on a phone
// connection. The dictionary is decompressed as it arrives, so peak disk use
// is the finished database alone.
import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif
#if canImport(Compression)
import Compression
#endif

public let DEFAULT_PACK_URL = "https://github.com/magiccpp/trilex/releases/latest/download"
let USER_AGENT = "Thriauga-iOS/1.0"

public let MEDIA_PACKS = ["images": "media-images.db", "audio": "media-audio.db"]

public struct PackError: Error, LocalizedError, Equatable {
    public let message: String
    public init(_ m: String) { message = m }
    public var errorDescription: String? { message }
    public static let cancelled = PackError("cancelled")
}

public struct PackInfo: Identifiable, Equatable, Sendable {
    public let name: String
    public let title: String
    public let detail: String
    public let file: String
    public let rows: Int
    public let bytes: Int64
    public let sha256: String
    public let built: String
    public let url: String
    public var id: String { name }
    public var sizeMB: Double { Double(bytes) / 1_048_576 }
}

public struct DictionaryInfo: Equatable, Sendable {
    public let file: String
    public let bytes: Int64
    public let uncompressed: Int64
    public let sha256: String
    public let built: String
    public let url: String
    public var sizeMB: Double { Double(bytes) / 1_048_576 }
}

public struct Manifest: Equatable, Sendable {
    public var packs: [PackInfo] = []
    public var dictionary: DictionaryInfo?
    /// A trimmed build for phones, when the release carries one.
    public var mobileDictionary: DictionaryInfo?

    /// What a phone should download: the trimmed build if published.
    public var preferredDictionary: DictionaryInfo? { mobileDictionary ?? dictionary }
}

/// Where everything lives on the device.
public struct TrilexPaths: Sendable {
    public let base: URL

    public init(base: URL) { self.base = base }

    /// Application Support/trilex, created on first use.
    public static func standard() -> TrilexPaths {
        let fm = FileManager.default
        let root = (try? fm.url(for: .applicationSupportDirectory, in: .userDomainMask,
                                appropriateFor: nil, create: true))
            ?? fm.temporaryDirectory
        let base = root.appendingPathComponent("trilex", isDirectory: true)
        try? fm.createDirectory(at: base, withIntermediateDirectories: true)
        return TrilexPaths(base: base)
    }

    public var dictionary: URL { base.appendingPathComponent("dict.db") }
    public var userDB: URL { base.appendingPathComponent("user.db") }
    public func pack(_ name: String) -> URL { base.appendingPathComponent(MEDIA_PACKS[name] ?? "media-\(name).db") }

    static func usable(_ url: URL, minBytes: Int64) -> Bool {
        let size = (try? FileManager.default.attributesOfItem(atPath: url.path)[.size] as? NSNumber)?.int64Value ?? 0
        return size > minBytes
    }

    public var dictionaryInstalled: Bool { Self.usable(dictionary, minBytes: 10 * 1_048_576) }
    public func packInstalled(_ name: String) -> Bool { Self.usable(pack(name), minBytes: 4096) }

    /// The dictionary and packs are re-downloadable; keep them out of
    /// iCloud backups. The wordbook is not excluded.
    public static func excludeFromBackup(_ url: URL) {
        #if canImport(Darwin)
        var u = url
        var v = URLResourceValues()
        v.isExcludedFromBackup = true
        try? u.setResourceValues(v)
        #endif
    }
}

// ---------------------------------------------------------- media attachment
public extension Lexicon {
    /// Open dict.db and ATTACH whichever packs are installed. SQLite resolves
    /// an unqualified table name across attached databases, so queries
    /// against `image` and `audio` need no changes either way.
    static func open(at url: URL, packsIn paths: TrilexPaths? = nil, readOnly: Bool = false) throws -> Lexicon {
        let db = try SQLiteDB(path: url.path, readOnly: readOnly)
        let d = Lexicon(db: db)
        if let paths {
            for name in MEDIA_PACKS.keys.sorted() { d.attachPack(name, paths: paths) }
        }
        return d
    }

    /// ATTACH a single pack, e.g. one installed while the app is running.
    @discardableResult
    func attachPack(_ name: String, paths: TrilexPaths) -> Bool {
        let alias = "media_\(name)"
        let attached = ((try? db.query("PRAGMA database_list")) ?? []).compactMap { $0.string("name") }
        if attached.contains(alias) { return true }
        guard paths.packInstalled(name) else { return false }
        do {
            try db.run("ATTACH DATABASE ? AS \(alias)", [.text(paths.pack(name).path)])
            refreshMedia()
            return true
        } catch {
            return false
        }
    }

    @discardableResult
    func detachPack(_ name: String) -> Bool {
        defer { refreshMedia() }
        return (try? db.run("DETACH DATABASE media_\(name)")) != nil
    }
}

// ------------------------------------------------------------------ download
/// Streams one HTTP response through callbacks, so a 200 MB body never sits
/// in memory.
final class StreamDelegate: NSObject, URLSessionDataDelegate, @unchecked Sendable {
    let onResponse: (HTTPURLResponse) throws -> Void
    let onData: (Data) throws -> Void
    var continuation: CheckedContinuation<Void, Error>?
    var failure: Error?

    init(onResponse: @escaping (HTTPURLResponse) throws -> Void, onData: @escaping (Data) throws -> Void) {
        self.onResponse = onResponse
        self.onData = onData
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse,
                    completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        do {
            guard let http = response as? HTTPURLResponse else { throw PackError("Not an HTTP response") }
            try onResponse(http)
            completionHandler(.allow)
        } catch {
            failure = error
            completionHandler(.cancel)
        }
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        guard failure == nil else { return }
        do {
            try onData(data)
        } catch {
            failure = error
            dataTask.cancel()
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        let c = continuation
        continuation = nil
        if let f = failure {
            c?.resume(throwing: f)
        } else if let e = error {
            if (e as? URLError)?.code == .cancelled {
                c?.resume(throwing: PackError.cancelled)
            } else {
                c?.resume(throwing: PackError("Download failed (\(e.localizedDescription))"))
            }
        } else {
            c?.resume()
        }
    }
}

func stream(_ request: URLRequest, onResponse: @escaping (HTTPURLResponse) throws -> Void,
            onData: @escaping (Data) throws -> Void) async throws {
    let delegate = StreamDelegate(onResponse: onResponse, onData: onData)
    let queue = OperationQueue()
    queue.maxConcurrentOperationCount = 1
    let session = URLSession(configuration: .default, delegate: delegate, delegateQueue: queue)
    defer { session.finishTasksAndInvalidate() }
    let task = session.dataTask(with: request)
    try await withTaskCancellationHandler {
        try await withCheckedThrowingContinuation { (c: CheckedContinuation<Void, Error>) in
            queue.addOperation {
                delegate.continuation = c
                task.resume()
            }
        }
    } onCancel: {
        task.cancel()
    }
}

/// Writes a decompressed stream to disk. Only xz is ever served; Apple's
/// Compression framework reads the xz container natively.
final class Sink {
    let handle: FileHandle
    #if canImport(Compression)
    var filter: OutputFilter?
    #endif

    init(_ url: URL, xz: Bool) throws {
        _ = FileManager.default.createFile(atPath: url.path, contents: nil)
        handle = try FileHandle(forWritingTo: url)
        if xz {
            #if canImport(Compression)
            let h = handle
            filter = try OutputFilter(.decompress, using: .lzma) { data in
                if let data { h.write(data) }
            }
            #else
            try? handle.close()
            throw PackError("xz decompression is not available on this platform")
            #endif
        }
    }

    func write(_ d: Data) throws {
        #if canImport(Compression)
        if let filter {
            try filter.write(d)
            return
        }
        #endif
        handle.write(d)
    }

    func finish() throws {
        #if canImport(Compression)
        try filter?.finalize()
        #endif
        try handle.close()
    }
}

public typealias Progress = @Sendable (Int64, Int64) -> Void

public final class PackClient: @unchecked Sendable {
    public let baseURL: String
    public let paths: TrilexPaths
    let transport: HTTPTransport

    public init(baseURL: String = DEFAULT_PACK_URL, paths: TrilexPaths,
                transport: HTTPTransport = URLSessionTransport()) {
        self.baseURL = baseURL.trimmingTrailingSlashes()
        self.paths = paths
        self.transport = transport
    }

    var isStatic: Bool { baseURL.contains("/releases/") || baseURL.hasSuffix("/download") }

    // ------------------------------------------------------------- discovery
    /// Static hosts serve manifest.json directly; the API serves /v1/packs.
    var manifestURL: String { isStatic ? "\(baseURL)/manifest.json" : "\(baseURL)/v1/packs" }

    func assetURL(_ entry: [String: Any], kind: String) -> String {
        if let u = entry["url"] as? String, !u.isEmpty { return u }
        let file = entry["file"] as? String ?? ""
        if isStatic { return "\(baseURL)/\(file)" }
        return kind == "dictionary" ? "\(baseURL)/v1/dictionary"
            : "\(baseURL)/v1/packs/\(entry["name"] as? String ?? "")"
    }

    public func manifest() async throws -> Manifest {
        guard let url = URL(string: manifestURL) else { throw PackError("Bad address \(manifestURL)") }
        var req = URLRequest(url: url, timeoutInterval: 25)
        req.setValue(USER_AGENT, forHTTPHeaderField: "User-Agent")
        let data: Data, status: Int
        do {
            (data, status) = try await transport.send(req)
        } catch {
            throw PackError("Cannot reach \(baseURL) (\(error.localizedDescription))")
        }
        guard (200..<300).contains(status),
              let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else {
            throw PackError("Manifest unavailable (HTTP \(status))")
        }
        return parse(obj)
    }

    func parse(_ obj: [String: Any]) -> Manifest {
        var m = Manifest()
        for p in (obj["packs"] as? [[String: Any]]) ?? [] {
            guard let name = p["name"] as? String, let file = p["file"] as? String,
                  let bytes = jsonNumber(p["bytes"]) else { continue }
            m.packs.append(PackInfo(name: name, title: p["title"] as? String ?? name,
                                    detail: p["detail"] as? String ?? "", file: file,
                                    rows: Int(jsonNumber(p["rows"]) ?? 0), bytes: Int64(bytes),
                                    sha256: p["sha256"] as? String ?? "",
                                    built: p["built"] as? String ?? "",
                                    url: assetURL(p, kind: "pack")))
        }
        func dict(_ key: String) -> DictionaryInfo? {
            guard let d = obj[key] as? [String: Any], let file = d["file"] as? String else { return nil }
            return DictionaryInfo(file: file, bytes: Int64(jsonNumber(d["bytes"]) ?? 0),
                                  uncompressed: Int64(jsonNumber(d["uncompressed"]) ?? 0),
                                  sha256: d["sha256"] as? String ?? "",
                                  built: d["built"] as? String ?? "",
                                  url: assetURL(d, kind: "dictionary"))
        }
        m.dictionary = dict("dictionary")
        m.mobileDictionary = dict("dictionary_mobile")
        return m
    }

    public func isInstalled(_ name: String) -> Bool { paths.packInstalled(name) }

    public func remove(_ name: String) {
        try? FileManager.default.removeItem(at: paths.pack(name))
    }

    // -------------------------------------------------------------- download
    /// Fetch a pack, resuming any partial file, then verify and install.
    /// Cancelling the task leaves the .part file so a later attempt continues.
    @discardableResult
    public func download(_ pack: PackInfo, progress: Progress? = nil) async throws -> URL {
        let fm = FileManager.default
        try fm.createDirectory(at: paths.base, withIntermediateDirectories: true)
        let final = paths.pack(pack.name)
        let part = final.appendingPathExtension("part")
        var have = (try? fm.attributesOfItem(atPath: part.path)[.size] as? NSNumber)?.int64Value ?? 0
        if have > pack.bytes {
            try? fm.removeItem(at: part)
            have = 0
        }
        if !fm.fileExists(atPath: part.path) { _ = fm.createFile(atPath: part.path, contents: nil) }
        guard let url = URL(string: pack.url) else { throw PackError("Bad address \(pack.url)") }
        var req = URLRequest(url: url, timeoutInterval: 60)
        req.setValue(USER_AGENT, forHTTPHeaderField: "User-Agent")
        if have > 0 { req.setValue("bytes=\(have)-", forHTTPHeaderField: "Range") }

        let fh = try FileHandle(forWritingTo: part)
        defer { try? fh.close() }
        var done = have
        try await stream(req, onResponse: { http in
            guard (200..<300).contains(http.statusCode) else {
                throw PackError("Download failed (HTTP \(http.statusCode))")
            }
            if have > 0 && http.statusCode != 206 {
                // Server ignored the Range; start over rather than append to
                // a partial file and corrupt it.
                fh.truncateFile(atOffset: 0)
                done = 0
            } else {
                fh.seekToEndOfFile()
            }
        }, onData: { d in
            fh.write(d)
            done += Int64(d.count)
            progress?(done, pack.bytes)
        })
        try fh.synchronize()

        let size = (try? fm.attributesOfItem(atPath: part.path)[.size] as? NSNumber)?.int64Value ?? 0
        if size != pack.bytes {
            throw PackError("Size mismatch: expected \(pack.bytes) bytes, got \(size)")
        }
        if !pack.sha256.isEmpty, try Digest256.file(part) != pack.sha256 {
            try? fm.removeItem(at: part)
            throw PackError("Checksum mismatch — the download was corrupted")
        }
        try? fm.removeItem(at: final)
        try fm.moveItem(at: part, to: final)
        TrilexPaths.excludeFromBackup(final)
        return final
    }

    /// Fetch and decompress the dictionary in one pass. The caller must close
    /// any open connection to the old dictionary before this replaces it.
    @discardableResult
    public func downloadDictionary(_ info: DictionaryInfo, progress: Progress? = nil) async throws -> URL {
        let fm = FileManager.default
        try fm.createDirectory(at: paths.base, withIntermediateDirectories: true)
        let final = paths.dictionary
        let part = paths.base.appendingPathComponent("dict.db.part")
        try? fm.removeItem(at: part)
        guard let url = URL(string: info.url) else { throw PackError("Bad address \(info.url)") }
        var req = URLRequest(url: url, timeoutInterval: 90)
        req.setValue(USER_AGENT, forHTTPHeaderField: "User-Agent")

        let sink = try Sink(part, xz: info.file.hasSuffix(".xz"))
        var digest = Digest256()
        var done: Int64 = 0
        do {
            try await stream(req, onResponse: { http in
                guard (200..<300).contains(http.statusCode) else {
                    throw PackError("Download failed (HTTP \(http.statusCode))")
                }
            }, onData: { d in
                digest.update(d)
                try sink.write(d)
                done += Int64(d.count)
                progress?(done, info.bytes)
            })
            try sink.finish()
        } catch {
            try? sink.finish()
            try? fm.removeItem(at: part)
            throw (error as? PackError) ?? PackError("Download failed (\(error.localizedDescription))")
        }
        if !info.sha256.isEmpty && digest.hex() != info.sha256 {
            try? fm.removeItem(at: part)
            throw PackError("Checksum mismatch — the download was corrupted")
        }
        for suffix in ["", "-wal", "-shm"] {
            try? fm.removeItem(at: URL(fileURLWithPath: final.path + suffix))
        }
        try fm.moveItem(at: part, to: final)
        TrilexPaths.excludeFromBackup(final)
        return final
    }
}
