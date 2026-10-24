import Foundation
import Observation
import TrilexKit
import UIKit

/// App-wide state: the open dictionary, the wordbook, sync and downloads.
///
/// The dictionary is opened from Application Support (downloaded on first
/// launch) or, if a build bundles `dict.db`, straight from the app bundle.
@MainActor
@Observable
final class AppModel {
    let paths: TrilexPaths
    let book: Wordbook
    let sync: SyncClient
    let packs: PackClient

    private(set) var lexicon: Lexicon?
    /// Bumped whenever the wordbook changes, so views that read it refresh.
    private(set) var bookVersion = 0
    /// Bumped whenever a media pack is installed or removed.
    private(set) var packsVersion = 0
    private(set) var recent: [String]

    private(set) var signedIn: Bool
    private(set) var syncing = false
    var syncStatus: String?

    private(set) var manifest: Manifest?
    private(set) var manifestError: String?

    private(set) var dictRunning = false
    private(set) var dictProgress: (done: Int64, total: Int64) = (0, 0)
    private(set) var dictError: String?

    private(set) var packProgress: [String: Double] = [:]
    private(set) var packError: [String: String] = [:]

    @ObservationIgnored private var packTasks: [String: Task<Void, Never>] = [:]
    @ObservationIgnored private var dictTask: Task<Void, Never>?
    @ObservationIgnored private var pendingSync: Task<Void, Never>?

    private static let recentKey = "recentSearches"
    private static let retentionKey = "retentionTarget"
    private static let dictShaKey = "dictionarySha256"

    init() {
        let paths = TrilexPaths.standard()
        self.paths = paths
        let book = (try? Wordbook(path: paths.userDB.path))
            ?? (try! Wordbook(db: SQLiteDB(path: ":memory:")))
        let target = UserDefaults.standard.double(forKey: Self.retentionKey)
        book.retentionTarget = target > 0 ? target : Forgetting.DEFAULT_RETENTION
        self.book = book
        let sync = SyncClient(book: book, deviceName: "iOS \(UIDevice.current.model)")
        self.sync = sync
        self.packs = PackClient(paths: paths)
        self.signedIn = sync.signedIn
        self.recent = UserDefaults.standard.stringArray(forKey: Self.recentKey) ?? []
        openDictionary()
    }

    // ------------------------------------------------------------ dictionary
    func openDictionary() {
        lexicon = nil
        var lex: Lexicon?
        if paths.dictionaryInstalled {
            lex = try? Lexicon.open(at: paths.dictionary, packsIn: paths)
        } else if let bundled = Bundle.main.url(forResource: "dict", withExtension: "db") {
            lex = try? Lexicon.open(at: bundled, packsIn: paths, readOnly: true)
        }
        guard let lex, lex.isUsable else { return }
        lexicon = lex
        // The typo index takes a moment to build; do it before it is needed.
        Task.detached(priority: .utility) { lex.warmUp() }
    }

    var installedDictionarySha: String? { UserDefaults.standard.string(forKey: Self.dictShaKey) }

    var dictionarySizeMB: Double {
        let size = (try? FileManager.default.attributesOfItem(atPath: paths.dictionary.path)[.size]
                    as? NSNumber)?.int64Value ?? 0
        return Double(size) / 1_048_576
    }

    func loadManifest(force: Bool = false) async {
        if manifest != nil && !force { return }
        manifestError = nil
        do {
            manifest = try await packs.manifest()
        } catch {
            manifestError = error.localizedDescription
        }
    }

    /// First run, or "update dictionary": fetch, verify, swap in, reopen.
    func downloadDictionary() {
        guard dictTask == nil else { return }
        dictError = nil
        dictRunning = true
        dictProgress = (0, 0)
        let app = UIApplication.shared
        app.isIdleTimerDisabled = true
        let bg = app.beginBackgroundTask(withName: "dictionary")
        dictTask = Task {
            defer {
                app.isIdleTimerDisabled = false
                app.endBackgroundTask(bg)
                dictRunning = false
                dictTask = nil
            }
            do {
                await loadManifest()
                guard let info = manifest?.preferredDictionary else {
                    throw PackError(manifestError ?? "No dictionary has been published yet.")
                }
                dictProgress = (0, info.bytes)
                let throttle = Throttle()
                let staged = try await packs.downloadDictionary(info) { done, total in
                    guard throttle.shouldReport(done, total) else { return }
                    Task { @MainActor in self.dictProgress = (done, total) }
                }
                lexicon = nil
                try packs.installDictionary(staged: staged)
                UserDefaults.standard.set(info.sha256, forKey: Self.dictShaKey)
                openDictionary()
                if lexicon == nil { throw PackError("The downloaded dictionary could not be opened.") }
            } catch let e as PackError where e == .cancelled {
                if lexicon == nil { openDictionary() }
            } catch {
                dictError = error.localizedDescription
                if lexicon == nil { openDictionary() }
            }
        }
    }

    func cancelDictionaryDownload() { dictTask?.cancel() }

    // ----------------------------------------------------------- media packs
    func isInstalled(_ name: String) -> Bool {
        _ = packsVersion
        return packs.isInstalled(name)
    }

    func isDownloading(_ name: String) -> Bool { packProgress[name] != nil }

    func downloadPack(_ pack: PackInfo) {
        guard packTasks[pack.name] == nil else { return }
        packError[pack.name] = nil
        packProgress[pack.name] = 0
        let bg = UIApplication.shared.beginBackgroundTask(withName: pack.name)
        packTasks[pack.name] = Task {
            defer {
                UIApplication.shared.endBackgroundTask(bg)
                packProgress[pack.name] = nil
                packTasks[pack.name] = nil
            }
            do {
                let throttle = Throttle()
                try await packs.download(pack) { done, total in
                    guard throttle.shouldReport(done, total) else { return }
                    Task { @MainActor in
                        if self.packProgress[pack.name] != nil {
                            self.packProgress[pack.name] = Double(done) / Double(max(total, 1))
                        }
                    }
                }
                lexicon?.attachPack(pack.name, paths: paths)
                packsVersion += 1
            } catch let e as PackError where e == .cancelled {
                // The partial file stays; the next attempt resumes it.
            } catch {
                packError[pack.name] = error.localizedDescription
            }
        }
    }

    func cancelPack(_ name: String) { packTasks[name]?.cancel() }

    func removePack(_ name: String) {
        lexicon?.detachPack(name)
        packs.remove(name)
        packsVersion += 1
    }

    // --------------------------------------------------------------- wordbook
    func isSaved(lang: String, word: String) -> Bool {
        _ = bookVersion
        return book.has(lang: lang, word: word)
    }

    func toggleSave(_ result: LookupResult) {
        guard let e = result.entries.first else { return }
        if book.has(lang: e.lang, word: e.word), let card = try? book.get(lang: e.lang, word: e.word) {
            try? book.remove(card.id)
        } else {
            try? book.add(lang: e.lang, word: e.word, snapshot: result.snapshot)
        }
        bookChanged()
    }

    func remove(_ card: Card) {
        try? book.remove(card.id)
        bookChanged()
    }

    func setNote(_ card: Card, _ note: String) {
        try? book.setNote(card.id, note)
        bookChanged()
    }

    func grade(_ card: Card, _ g: Grade) {
        try? book.grade(card, g)
        bookChanged()
    }

    var counts: WordbookCounts {
        _ = bookVersion
        return book.counts()
    }

    var retentionTarget: Double {
        get { _ = bookVersion; return book.retentionTarget }
        set {
            book.retentionTarget = newValue
            UserDefaults.standard.set(newValue, forKey: Self.retentionKey)
            bookVersion += 1
        }
    }

    func bookChanged() {
        bookVersion += 1
        scheduleSync()
    }

    func addRecent(_ word: String) {
        recent.removeAll { $0 == word }
        recent.insert(word, at: 0)
        recent = Array(recent.prefix(30))
        UserDefaults.standard.set(recent, forKey: Self.recentKey)
    }

    func clearRecent() {
        recent = []
        UserDefaults.standard.removeObject(forKey: Self.recentKey)
    }

    /// The app came to the foreground: the day may have changed, and another
    /// device may have synced.
    func becameActive() {
        bookVersion += 1
        if signedIn { scheduleSync(after: 0) }
    }

    // ------------------------------------------------------------------- sync
    func scheduleSync(after seconds: Double = 2) {
        guard signedIn else { return }
        pendingSync?.cancel()
        pendingSync = Task {
            try? await Task.sleep(for: .seconds(seconds))
            guard !Task.isCancelled else { return }
            await runSync()
        }
    }

    func runSync() async {
        guard signedIn, !syncing else { return }
        syncing = true
        defer { syncing = false }
        do {
            let r = try await sync.sync()
            syncStatus = r.summary
            if r.applied > 0 { bookVersion += 1 }
        } catch {
            syncStatus = error.localizedDescription
            if (error as? SyncError)?.message.hasPrefix("Session expired") == true {
                sync.signOut()
                signedIn = false
            }
        }
    }

    func setServer(_ url: String) {
        let u = url.trimmingCharacters(in: .whitespacesAndNewlines)
        sync.baseURL = u.isEmpty ? DEFAULT_SYNC_URL : u
    }

    func requestCode(email: String) async throws {
        try await sync.requestCode(email: email.trimmingCharacters(in: .whitespacesAndNewlines))
    }

    func signIn(email: String, code: String) async throws {
        try await sync.verify(email: email.trimmingCharacters(in: .whitespacesAndNewlines),
                              code: code.trimmingCharacters(in: .whitespacesAndNewlines))
        signedIn = true
        await runSync()
    }

    func signOut() {
        pendingSync?.cancel()
        sync.signOut()
        signedIn = false
        syncStatus = nil
    }

    // ------------------------------------------------------------------ audio
    func play(lang: String, word: String) {
        guard let lex = lexicon else { return }
        Task {
            let data = await Task.detached { lex.audioBlob(lang, word) }.value
            if let data { AudioPlayer.shared.play(data) }
        }
    }
}

/// Report progress at most once per percent, so a 200 MB download does not
/// post thousands of UI updates.
final class Throttle: @unchecked Sendable {
    private let lock = NSLock()
    private var last = -1

    func shouldReport(_ done: Int64, _ total: Int64) -> Bool {
        let pct = total > 0 ? Int(done * 100 / total) : 0
        lock.lock()
        defer { lock.unlock() }
        if pct == last { return false }
        last = pct
        return true
    }
}
