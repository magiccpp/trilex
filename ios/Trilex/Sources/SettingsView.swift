import SwiftUI
import TrilexKit

struct SettingsView: View {
    var body: some View {
        NavigationStack {
            Form {
                SyncSection()
                AddOnsSection()
                ReviewSection()
                DictionarySection()
                AboutSection()
            }
            .navigationTitle("Settings")
        }
    }
}

// ------------------------------------------------------------------- sync
/// Passwordless sign-in and manual sync. Sync is opt-in and covers the
/// wordbook only - the dictionary itself is never sent anywhere.
private struct SyncSection: View {
    @Environment(AppModel.self) private var model
    @State private var email = ""
    @State private var code = ""
    @State private var server = ""
    @State private var codeSent = false
    @State private var busy = false
    @State private var message: String?

    var body: some View {
        Section {
            if model.signedIn {
                LabeledContent("Signed in as", value: model.sync.email ?? "")
                if let last = model.sync.lastSync {
                    LabeledContent("Last sync", value: last.replacingOccurrences(of: "T", with: " "))
                }
                Button {
                    Task { await model.runSync() }
                } label: {
                    HStack {
                        Text("Sync now")
                        if model.syncing {
                            Spacer()
                            ProgressView()
                        }
                    }
                }
                .disabled(model.syncing)
                Button("Sign out", role: .destructive) { model.signOut() }
            } else {
                TextField("Email", text: $email)
                    .keyboardType(.emailAddress)
                    .textContentType(.emailAddress)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                Button(codeSent ? "Send another code" : "Send code") {
                    Task { await send() }
                }
                .disabled(busy || !email.contains("@"))
                if codeSent {
                    TextField("6-digit code from your email", text: $code)
                        .keyboardType(.numberPad)
                        .textContentType(.oneTimeCode)
                    Button("Sign in") { Task { await verify() } }
                        .disabled(busy || code.count < 4)
                }
                DisclosureGroup("Server") {
                    TextField(DEFAULT_SYNC_URL, text: $server)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                }
            }
            if let text = message ?? model.syncStatus {
                Text(text).font(.footnote).foregroundStyle(Theme.muted)
            }
        } header: {
            Text("Sync")
        } footer: {
            Text("Optional. Only your wordbook is synced, with the desktop app or another phone. "
                 + "The dictionary never leaves this device.")
        }
        .onAppear {
            if server.isEmpty && model.sync.baseURL != DEFAULT_SYNC_URL { server = model.sync.baseURL }
            if email.isEmpty { email = model.sync.email ?? "" }
        }
    }

    private func send() async {
        busy = true
        defer { busy = false }
        model.setServer(server)
        do {
            try await model.requestCode(email: email)
            codeSent = true
            message = "Code sent. Check your email."
        } catch {
            message = error.localizedDescription
        }
    }

    private func verify() async {
        busy = true
        defer { busy = false }
        do {
            try await model.signIn(email: email, code: code)
            code = ""
            codeSent = false
            message = nil
        } catch {
            message = error.localizedDescription
        }
    }
}

// --------------------------------------------------------------- add-ons
private struct AddOnsSection: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        Section {
            if let m = model.manifest {
                ForEach(m.packs) { PackRow(pack: $0) }
            } else if let err = model.manifestError {
                Text(err).font(.footnote).foregroundStyle(Theme.muted)
                Button("Try again") { Task { await model.loadManifest(force: true) } }
            } else {
                ProgressView()
            }
            // Offline, the manifest is unreachable but installed packs can
            // still be removed.
            if model.manifest == nil {
                ForEach(MEDIA_PACKS.keys.sorted().filter { model.isInstalled($0) }, id: \.self) { name in
                    HStack {
                        Text(name.capitalized)
                        Spacer()
                        Button("Remove", role: .destructive) { model.removePack(name) }
                    }
                }
            }
        } header: {
            Text("Add-ons")
        } footer: {
            Text("Illustrations and pronunciation audio are optional. Once downloaded they work offline.")
        }
        .task { await model.loadManifest() }
    }
}

private struct PackRow: View {
    let pack: PackInfo
    @Environment(AppModel.self) private var model

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text(pack.title)
                    Text(pack.detail).font(.caption).foregroundStyle(Theme.muted)
                }
                Spacer()
                if model.isDownloading(pack.name) {
                    Button("Cancel") { model.cancelPack(pack.name) }
                } else if model.isInstalled(pack.name) {
                    Button("Remove", role: .destructive) { model.removePack(pack.name) }
                } else {
                    Button(String(format: "Get %.0f MB", pack.sizeMB)) { model.downloadPack(pack) }
                        .buttonStyle(.bordered)
                }
            }
            .buttonStyle(.borderless)
            if let p = model.packProgress[pack.name] {
                ProgressView(value: p)
            }
            if let err = model.packError[pack.name] {
                Text(err).font(.caption).foregroundStyle(.red)
            }
        }
    }
}

// ----------------------------------------------------------------- review
private struct ReviewSection: View {
    @Environment(AppModel.self) private var model
    @AppStorage("autoplayAudio") private var autoplay = true

    var body: some View {
        @Bindable var model = model
        Section {
            VStack(alignment: .leading) {
                LabeledContent("Target recall", value: Forgetting.fmtRetention(model.retentionTarget))
                Slider(value: $model.retentionTarget, in: 0.80...0.95, step: 0.01)
            }
            Toggle("Play pronunciation on reveal", isOn: $autoplay)
        } header: {
            Text("Review")
        } footer: {
            Text("A card comes back when your predicted recall falls to the target. "
                 + "Higher means more frequent reviews.")
        }
    }
}

// ------------------------------------------------------------- dictionary
private struct DictionarySection: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        Section("Dictionary") {
            if model.paths.dictionaryInstalled {
                LabeledContent("Installed", value: String(format: "%.0f MB", model.dictionarySizeMB))
            }
            if model.dictRunning {
                let p = model.dictProgress
                ProgressView(value: p.total > 0 ? min(1, Double(p.done) / Double(p.total)) : 0)
                Button("Cancel") { model.cancelDictionaryDownload() }
            } else if let info = model.manifest?.preferredDictionary {
                if info.sha256 == model.installedDictionarySha {
                    LabeledContent("Status", value: "Up to date")
                } else {
                    Button(String(format: "Download update (%.0f MB)", info.sizeMB)) {
                        model.downloadDictionary()
                    }
                }
            }
            if let err = model.dictError {
                Text(err).font(.footnote).foregroundStyle(.red)
            }
        }
    }
}

// ------------------------------------------------------------------ about
private struct AboutSection: View {
    var body: some View {
        Section {
            LabeledContent("Version", value: Bundle.main.object(
                forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "")
            NavigationLink("Data sources & licences") { SourcesView() }
            Link("Source code", destination: URL(string: "https://github.com/magiccpp/trilex")!)
        } header: {
            Text("About")
        } footer: {
            Text("Þríauga — Old Norse þrí- “three” + auga “eye”. Odin gave one eye at Mímir's well "
                 + "for wisdom; this gives you three, one per tongue.")
        }
    }
}

private struct SourcesView: View {
    private let sources: [(String, String, String)] = [
        ("CC-CEDICT", "Chinese ↔ English entries", "CC BY-SA 4.0"),
        ("Folkets lexikon", "Swedish ↔ English entries", "CC BY-SA 2.5"),
        ("Wiktionary via wiktextract", "Entries, etymologies, inflections, translations", "CC BY-SA 4.0 / GFDL"),
        ("Tatoeba", "Aligned example sentences", "CC BY 2.0 FR"),
        ("Wikimedia Commons", "Illustrations and pronunciation recordings", "Per file, shown in the app"),
    ]

    var body: some View {
        List {
            Section {
                ForEach(sources, id: \.0) { s in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(s.0)
                        Text(s.1).font(.caption).foregroundStyle(Theme.muted)
                        Text(s.2).font(.caption).foregroundStyle(Theme.faint)
                    }
                }
            } footer: {
                Text("No free Chinese–Swedish lexicon exists, so those pairs are derived through English "
                     + "and labelled “Matched through English”.")
            }
        }
        .navigationTitle("Data sources")
    }
}
