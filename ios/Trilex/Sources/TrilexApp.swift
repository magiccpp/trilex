import SwiftUI
import TrilexKit
import UIKit

@main
struct TrilexApp: App {
    @State private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(model)
        }
    }
}

struct RootView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        Group {
            if model.lexicon == nil {
                SetupView()
            } else {
                TabView {
                    SearchView()
                        .tabItem { Label("Search", systemImage: "magnifyingglass") }
                    WordbookView()
                        .tabItem { Label("Wordbook", systemImage: "book") }
                    ReviewView()
                        .tabItem { Label("Review", systemImage: "rectangle.stack") }
                        .badge(model.counts.due)
                    SettingsView()
                        .tabItem { Label("Settings", systemImage: "gearshape") }
                }
            }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { model.becameActive() }
        }
    }
}

/// First launch: the dictionary is fetched once, then everything is offline.
struct SetupView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        VStack(spacing: 22) {
            Spacer()
            Text("文")
                .font(.system(size: 64, weight: .bold))
                .foregroundStyle(.white)
                .frame(width: 112, height: 112)
                .background(Color(uiColor: UIColor(rgb: 0x1A5FB4)),
                            in: RoundedRectangle(cornerRadius: 26, style: .continuous))
                .accessibilityHidden(true)
            VStack(spacing: 6) {
                Text("Þríauga").font(.largeTitle.weight(.semibold))
                Text("English · Svenska · 中文").foregroundStyle(Theme.muted)
            }
            Text(blurb)
                .multilineTextAlignment(.center)
                .foregroundStyle(Theme.muted)

            if model.dictRunning {
                VStack(spacing: 10) {
                    ProgressView(value: fraction)
                    Text(progressText).font(.footnote.monospacedDigit()).foregroundStyle(Theme.muted)
                    Button("Cancel", role: .cancel) { model.cancelDictionaryDownload() }
                }
            } else {
                Button {
                    model.downloadDictionary()
                } label: {
                    Text("Download dictionary").frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                if let err = model.dictError {
                    Text(err).font(.footnote).foregroundStyle(.red).multilineTextAlignment(.center)
                }
            }
            Text("Wi-Fi recommended. The screen stays on until it finishes.")
                .font(.footnote)
                .foregroundStyle(Theme.faint)
            Spacer()
        }
        .padding(32)
        .background(Theme.background)
        .task { await model.loadManifest() }
    }

    private var blurb: String {
        let size = model.manifest?.preferredDictionary.map { String(format: " (%.0f MB)", $0.sizeMB) } ?? ""
        return "The dictionary is downloaded once\(size). After that everything works offline: "
            + "search, etymology, inflections, example sentences and review."
    }

    private var fraction: Double {
        let p = model.dictProgress
        return p.total > 0 ? min(1, Double(p.done) / Double(p.total)) : 0
    }

    private var progressText: String {
        let p = model.dictProgress
        return String(format: "%.0f of %.0f MB", Double(p.done) / 1_048_576, Double(p.total) / 1_048_576)
    }
}
