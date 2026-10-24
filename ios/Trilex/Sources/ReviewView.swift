import SwiftUI
import TrilexKit

/// Flashcards on the forgetting curve. The queue is ordered by predicted
/// recall, lowest first: the words closest to being forgotten come back first.
struct ReviewView: View {
    @Environment(AppModel.self) private var model
    @AppStorage("autoplayAudio") private var autoplay = true
    @State private var queue: [Card] = []
    @State private var card: Card?
    @State private var revealed = false
    @State private var page: Page?
    @State private var preview: [Grade: Double] = [:]

    var body: some View {
        NavigationStack {
            Group {
                if let card {
                    session(card)
                } else {
                    idle
                }
            }
            .background(Theme.background)
            .navigationTitle("Review")
            .navigationBarTitleDisplayMode(.inline)
            .wordDestinations()
        }
    }

    // ------------------------------------------------------------------ idle
    @ViewBuilder private var idle: some View {
        let n = model.counts
        if n.total == 0 {
            ContentUnavailableView("Your wordbook is empty", systemImage: "rectangle.stack",
                                   description: Text("Look up a word and tap ☆ to start collecting."))
        } else if n.due == 0 {
            ContentUnavailableView {
                Label("Nothing due today", systemImage: "checkmark.circle")
            } description: {
                if let next = model.book.nextDue() {
                    Text("Next review: \(next). All \(n.total) words are scheduled.")
                } else {
                    Text("All \(n.total) words are scheduled.")
                }
            }
        } else {
            VStack(spacing: 14) {
                Spacer()
                Text("\(n.due)").font(.system(size: 64, weight: .semibold)).monospacedDigit()
                Text(n.due == 1 ? "word ready to review" : "words ready to review")
                    .font(.title3)
                Text("\(n.new) new · \(n.review) returning").foregroundStyle(Theme.muted)
                Button {
                    start()
                } label: {
                    Text("Start review").frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .padding(.top, 12)
                Spacer()
            }
            .padding(32)
        }
    }

    // --------------------------------------------------------------- session
    private func session(_ card: Card) -> some View {
        let today = model.book.today
        return VStack(spacing: 0) {
            Text(status(card, today: today))
                .font(.caption)
                .foregroundStyle(Theme.muted)
                .padding(.vertical, 6)
            ScrollView {
                VStack(spacing: 0) {
                    VStack(spacing: 6) {
                        Text(card.word)
                            .font(Theme.wordFont(card.word, base: 44))
                            .multilineTextAlignment(.center)
                        LangBadge(lang: card.lang)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 36)

                    if revealed {
                        answer(card).padding(.horizontal, 20).padding(.bottom, 24)
                    }
                }
            }
            controls(card)
        }
    }

    @ViewBuilder private func answer(_ card: Card) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            if let page, page.found {
                PageView(page: page)
            } else if page != nil {
                // The dictionary no longer has it; show what was saved.
                Text(card.meaning(8)).foregroundStyle(Theme.muted)
            } else {
                ProgressView().frame(maxWidth: .infinity)
            }
            if !card.note.isEmpty {
                SectionHeader(title: "Your note")
                Note(card.note)
            }
        }
    }

    @ViewBuilder private func controls(_ card: Card) -> some View {
        Group {
            if revealed {
                HStack(spacing: 8) {
                    ForEach(Grade.allCases, id: \.self) { g in
                        Button {
                            grade(g)
                        } label: {
                            VStack(spacing: 2) {
                                Text(g.name).font(.subheadline.weight(.semibold))
                                Text(Forgetting.fmtInterval(preview[g] ?? 1)).font(.caption2)
                            }
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 4)
                        }
                        .buttonStyle(.bordered)
                        .tint(g == .again ? Color.red : Theme.accent)
                    }
                }
            } else {
                Button {
                    reveal()
                } label: {
                    Text("Show answer").frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(.bar)
    }

    private func status(_ card: Card, today: String) -> String {
        var bits = ["\(queue.count + 1) left", card.isNew ? "new" : "review"]
        if !card.isNew { bits.append("recall now \(Forgetting.fmtRetention(card.retention(today: today)))") }
        bits.append(String(format: "difficulty %.1f/10", card.difficulty))
        return bits.joined(separator: " · ")
    }

    // ---------------------------------------------------------------- flow
    private func start() {
        queue = model.book.due(limit: 200)
        next()
    }

    private func next() {
        page = nil
        revealed = false
        if queue.isEmpty {
            card = nil
        } else {
            card = queue.removeFirst()
        }
    }

    private func reveal() {
        guard let card else { return }
        preview = model.book.preview(card)
        revealed = true
        guard let lex = model.lexicon else {
            page = Page(LookupResult(query: card.word))
            return
        }
        let word = card.word
        Task {
            let r = await Task.detached(priority: .userInitiated) { lex.lookup(word) }.value
            guard self.card?.id == card.id else { return }
            page = Page(r)
            if autoplay, r.audio != nil, let e = r.entries.first {
                model.play(lang: e.lang, word: e.word)
            }
        }
    }

    private func grade(_ g: Grade) {
        guard let card, revealed else { return }
        model.grade(card, g)
        next()
    }
}
