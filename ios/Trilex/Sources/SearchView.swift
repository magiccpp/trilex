import SwiftUI
import TrilexKit

struct SearchView: View {
    @Environment(AppModel.self) private var model
    @State private var path: [WordLink] = []
    @State private var text = ""
    @State private var suggestions: [Suggestion] = []

    private var trimmed: String { text.trimmingCharacters(in: .whitespacesAndNewlines) }

    var body: some View {
        NavigationStack(path: $path) {
            List {
                if trimmed.isEmpty {
                    if !model.recent.isEmpty {
                        Section {
                            ForEach(model.recent, id: \.self) { w in
                                NavigationLink(value: WordLink(word: w, lang: "")) {
                                    Text(w).font(Theme.wordFont(w, base: 17).weight(.regular))
                                }
                            }
                        } header: {
                            HStack {
                                Text("Recent")
                                Spacer()
                                Button("Clear") { model.clearRecent() }.font(.footnote)
                            }
                        }
                    }
                    Section("Try") {
                        example("water", "an English word — see Swedish and Chinese at once")
                        example("vatten", "Swedish")
                        example("森林", "Chinese characters")
                        example("shuijiao", "pinyin, no tones needed")
                        example("watter", "a misspelling — suggestions appear")
                    }
                } else {
                    ForEach(suggestions, id: \.self) { s in
                        NavigationLink(value: WordLink(word: s.word, lang: s.lang)) {
                            HStack {
                                Text(s.word).font(Theme.wordFont(s.word, base: 17).weight(.regular))
                                Spacer()
                                LangBadge(lang: s.lang)
                            }
                        }
                    }
                    Button {
                        open(trimmed)
                    } label: {
                        Label("Look up “\(trimmed)”", systemImage: "arrow.right.circle")
                    }
                }
            }
            .listStyle(.insetGrouped)
            .navigationTitle("Þríauga")
            .searchable(text: $text, placement: .navigationBarDrawer(displayMode: .always),
                        prompt: "English, Svenska, 中文 or pinyin")
            .textInputAutocapitalization(.never)
            .autocorrectionDisabled()
            .onSubmit(of: .search) { open(trimmed) }
            .task(id: text) { await refreshSuggestions() }
            .wordDestinations()
        }
    }

    private func example(_ word: String, _ hint: String) -> some View {
        NavigationLink(value: WordLink(word: word, lang: "")) {
            VStack(alignment: .leading, spacing: 2) {
                Text(word).foregroundStyle(Theme.accent)
                Text(hint).font(.footnote).foregroundStyle(Theme.muted)
            }
        }
    }

    private func open(_ q: String) {
        guard !q.isEmpty else { return }
        path.append(WordLink(word: q, lang: ""))
    }

    /// Prefix completion runs off the main thread and is cancelled by the
    /// next keystroke, so typing never waits on the database.
    private func refreshSuggestions() async {
        let q = trimmed
        guard let lex = model.lexicon, !q.isEmpty else {
            suggestions = []
            return
        }
        try? await Task.sleep(for: .milliseconds(50))
        if Task.isCancelled { return }
        let found = await Task.detached(priority: .userInitiated) { lex.suggest(q) }.value
        if !Task.isCancelled { suggestions = found }
    }
}

/// One looked-up word.
struct ResultView: View {
    let query: String
    @Environment(AppModel.self) private var model
    @State private var result: LookupResult?
    @State private var page: Page?

    var body: some View {
        ScrollView {
            if let page {
                PageView(page: page)
                    .padding(.horizontal, 20)
                    .padding(.bottom, 32)
            } else {
                ProgressView().padding(.top, 80)
            }
        }
        .background(Theme.background)
        .navigationTitle(page?.header?.entry.word ?? query)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if let result, let e = result.entries.first {
                ToolbarItem(placement: .topBarTrailing) {
                    SaveButton(saved: model.isSaved(lang: e.lang, word: e.word)) {
                        model.toggleSave(result)
                    }
                }
            }
        }
        .task(id: query) { await load() }
    }

    private func load() async {
        guard let lex = model.lexicon else { return }
        let q = query
        let r = await Task.detached(priority: .userInitiated) { lex.lookup(q) }.value
        result = r
        page = Page(r)
        if let e = r.entries.first { model.addRecent(e.word) }
    }
}

struct SaveButton: View {
    let saved: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: saved ? "star.fill" : "star")
        }
        .accessibilityLabel(saved ? "Remove from wordbook" : "Add to wordbook")
        .sensoryFeedback(.selection, trigger: saved)
    }
}
