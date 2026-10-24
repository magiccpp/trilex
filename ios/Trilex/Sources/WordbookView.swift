import CoreTransferable
import SwiftUI
import TrilexKit
import UniformTypeIdentifiers

struct WordbookView: View {
    @Environment(AppModel.self) private var model
    @AppStorage("wordbookOrder") private var order: CardOrder = .due
    @State private var filter = ""
    @State private var editing: Card?

    private var cards: [Card] {
        _ = model.bookVersion
        let all = model.book.all(order: order)
        let f = Norm.norm(filter)
        guard !f.isEmpty else { return all }
        return all.filter { Norm.norm($0.word).contains(f) || Norm.norm($0.meaning(3)).contains(f) }
    }

    var body: some View {
        NavigationStack {
            let cards = self.cards
            let today = model.book.today
            List {
                if !cards.isEmpty {
                    Section {
                        ForEach(cards) { card in
                            NavigationLink(value: WordLink(word: card.word, lang: card.lang)) {
                                CardRow(card: card, today: today)
                            }
                            .swipeActions(edge: .trailing) {
                                Button(role: .destructive) {
                                    model.remove(card)
                                } label: {
                                    Label("Remove", systemImage: "trash")
                                }
                                Button {
                                    editing = card
                                } label: {
                                    Label("Note", systemImage: "square.and.pencil")
                                }
                                .tint(.orange)
                            }
                        }
                    } header: {
                        let n = model.counts
                        Text("\(n.total) saved · \(n.due) due today")
                    }
                }
            }
            .overlay {
                if cards.isEmpty {
                    if filter.isEmpty {
                        ContentUnavailableView("Your wordbook is empty", systemImage: "book",
                                               description: Text("Look up a word and tap ☆ to start collecting."))
                    } else {
                        ContentUnavailableView.search(text: filter)
                    }
                }
            }
            .searchable(text: $filter, prompt: "Filter")
            .navigationTitle("Wordbook")
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Menu {
                        Picker("Sort by", selection: $order) {
                            Text("Due").tag(CardOrder.due)
                            Text("Recently added").tag(CardOrder.added)
                            Text("Word").tag(CardOrder.word)
                            Text("Language").tag(CardOrder.lang)
                        }
                    } label: {
                        Label("Sort", systemImage: "arrow.up.arrow.down")
                    }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    ShareLink(item: WordbookCSV(book: model.book),
                              preview: SharePreview("wordbook.csv")) {
                        Label("Export CSV", systemImage: "square.and.arrow.up")
                    }
                    .disabled(cards.isEmpty)
                }
            }
            .sheet(item: $editing) { card in
                NoteEditor(card: card)
            }
            .wordDestinations()
        }
    }
}

private struct CardRow: View {
    let card: Card
    let today: String

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline) {
                Text(card.word).font(Theme.wordFont(card.word, base: 19))
                LangBadge(lang: card.lang)
                Spacer()
                Text(dueText).font(.footnote).foregroundStyle(urgent ? Color.orange : Theme.muted)
            }
            let meaning = card.meaning()
            if !meaning.isEmpty {
                Text(meaning).font(.subheadline).foregroundStyle(Theme.muted).lineLimit(1)
            }
            HStack(spacing: 12) {
                if !card.isNew {
                    let r = card.retention(today: today)
                    Text("recall \(Forgetting.fmtRetention(r))")
                        .foregroundStyle(r < 0.85 ? Color.orange : Theme.faint)
                    Text("\(card.reps) reviews").foregroundStyle(Theme.faint)
                }
                if !card.note.isEmpty {
                    Label(card.note, systemImage: "note.text").lineLimit(1).foregroundStyle(Theme.faint)
                }
            }
            .font(.caption)
        }
        .padding(.vertical, 2)
    }

    private var dueText: String {
        if card.isNew { return "new" }
        let d = Day.between(today, card.due) ?? 0
        return d <= 0 ? "due now" : "in \(d)d"
    }

    private var urgent: Bool { card.isNew || (Day.between(today, card.due) ?? 0) <= 0 }
}

private struct NoteEditor: View {
    let card: Card
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var text = ""

    var body: some View {
        NavigationStack {
            Form {
                TextField("Your note", text: $text, axis: .vertical)
                    .lineLimit(3...10)
            }
            .navigationTitle(card.word)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        model.setNote(card, text)
                        dismiss()
                    }
                }
            }
        }
        .presentationDetents([.medium])
        .onAppear { text = card.note }
    }
}

/// The wordbook as CSV, written only when the share sheet asks for it. A
/// byte-order mark makes Excel read the Swedish and Chinese correctly.
struct WordbookCSV: Transferable {
    let book: Wordbook

    static var transferRepresentation: some TransferRepresentation {
        FileRepresentation(exportedContentType: .commaSeparatedText) { csv in
            let url = FileManager.default.temporaryDirectory.appendingPathComponent("wordbook.csv")
            try Data(("\u{FEFF}" + csv.book.csv()).utf8).write(to: url, options: .atomic)
            return SentTransferredFile(url)
        }
    }
}
