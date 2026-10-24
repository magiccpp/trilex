import SwiftUI
import TrilexKit
import UIKit

/// A result page, section by section. Which sections appear and in what
/// order is decided by TrilexKit's `Page` (the port of render.py); this view
/// only lays them out.
struct PageView: View {
    let page: Page

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if !page.found {
                NotFound(page: page)
            } else {
                if let from = page.resolvedFrom, let h = page.header {
                    Chip(Text(from) + Text(" is a form of ") + Text(h.entry.word).bold())
                        .padding(.top, 16)
                }
                if let h = page.header {
                    HeaderView(header: h).padding(.top, 16)
                }
                notes
                definitions
                forms
                ForEach(page.translations, id: \.self) { TranslationSectionView(section: $0) }
                if let r = page.related { TranslationSectionView(section: r) }
                ForEach(page.examples, id: \.self) { ExampleSectionView(section: $0) }
                etymology
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder private var notes: some View {
        if page.wiktionaryOnly {
            Note(Page.wiktionaryOnlyNote).padding(.top, 10)
        }
        if let also = page.alsoFormOf {
            HStack(spacing: 4) {
                Note("also the \(also.tags) of")
                WordLinkView(word: also.link.word, lang: also.link.lang, size: 15)
            }
            .padding(.top, 8)
        }
        if !page.alsoPOS.isEmpty {
            Note("also: " + page.alsoPOS.joined(separator: " · ")).padding(.top, 6)
        }
    }

    @ViewBuilder private var definitions: some View {
        if !page.definitions.isEmpty {
            SectionHeader(title: "Definition")
            VStack(alignment: .leading, spacing: 6) {
                ForEach(Array(page.definitions.enumerated()), id: \.offset) { i, d in
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text("\(i + 1).").foregroundStyle(Theme.faint).monospacedDigit()
                        Text(d).textSelection(.enabled)
                    }
                }
            }
        }
    }

    @ViewBuilder private var forms: some View {
        if !page.forms.isEmpty {
            SectionHeader(title: "Forms")
            VStack(alignment: .leading, spacing: 6) {
                ForEach(Array(page.forms.enumerated()), id: \.offset) { _, f in
                    HStack(alignment: .firstTextBaseline, spacing: 10) {
                        Text(f.label).font(.subheadline).foregroundStyle(Theme.muted)
                        WordLinkView(word: f.link.word, lang: f.link.lang)
                    }
                }
            }
        }
    }

    @ViewBuilder private var etymology: some View {
        SectionHeader(title: "Etymology")
        if page.etymologies.isEmpty {
            Note(Page.noEtymologyNote)
        } else {
            VStack(alignment: .leading, spacing: 16) {
                ForEach(Array(page.etymologies.enumerated()), id: \.offset) { _, e in
                    VStack(alignment: .leading, spacing: 4) {
                        etymologyHead(e)
                        Text(e.text ?? "").textSelection(.enabled)
                    }
                    .ruled()
                }
            }
        }
    }

    private func etymologyHead(_ e: Etymology) -> Text {
        var t = Text(e.word).font(Theme.wordFont(e.word, base: 17))
        t = t + Text("  ") + Text(LANG_NAME[e.lang] ?? e.lang).font(.caption).foregroundStyle(Theme.faint)
        if let pos = e.pos { t = t + Text("  \(pos)").italic().foregroundStyle(Theme.muted) }
        if let ipa = e.ipa { t = t + Text("  \(ipa)").foregroundStyle(Theme.accent) }
        return t
    }
}

private struct NotFound: View {
    let page: Page

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Chip(Text(page.query).bold() + Text(" is not in the dictionary."))
                .padding(.top, 16)
            if page.didYouMean.isEmpty {
                Note("No similar words found either.").padding(.top, 12)
            } else {
                SectionHeader(title: "Did you mean")
                VStack(alignment: .leading, spacing: 10) {
                    ForEach(page.didYouMean, id: \.self) { s in
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            WordLinkView(word: s.word, lang: s.lang)
                            LangBadge(lang: s.lang)
                        }
                    }
                }
            }
        }
    }
}

private struct HeaderView: View {
    let header: Header
    @Environment(AppModel.self) private var model

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 6) {
                Text(header.entry.word)
                    .font(Theme.wordFont(header.entry.word, base: 34))
                    .textSelection(.enabled)
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    if let audio = header.audio {
                        Button {
                            model.play(lang: header.entry.lang, word: header.entry.word)
                        } label: {
                            Image(systemName: "speaker.wave.2.fill")
                        }
                        .accessibilityLabel("Play pronunciation")
                        if let accent = audio.accent { LangBadge(lang: accent) }
                    }
                    details
                }
            }
            Spacer(minLength: 0)
            if let image = header.image {
                Illustration(image: image, credit: header.imageCredit)
            }
        }
    }

    private var details: Text {
        let e = header.entry
        var t = Text("")
        if let r = e.reading { t = t + Text(r).foregroundStyle(Theme.accent) + Text("  ") }
        if let trad = e.traditional { t = t + Text("trad \(trad)  ").foregroundStyle(Theme.faint) }
        if let pos = e.pos { t = t + Text("\(pos)  ").italic().foregroundStyle(Theme.muted) }
        return t + Text(header.langName).font(.caption).foregroundStyle(Theme.faint)
    }
}

/// Illustrations are small freely-licensed images, shown with attribution.
private struct Illustration: View {
    let image: ImageInfo
    let credit: String?
    @Environment(AppModel.self) private var model
    @State private var ui: UIImage?

    var body: some View {
        VStack(alignment: .trailing, spacing: 4) {
            if let ui {
                Image(uiImage: ui)
                    .resizable()
                    .scaledToFit()
                    .frame(maxWidth: 120, maxHeight: 120)
                    .clipShape(RoundedRectangle(cornerRadius: 6))
                    .accessibilityLabel("Illustration of \(image.word)")
            }
            if let credit {
                Text(credit).font(.caption2).foregroundStyle(Theme.faint).lineLimit(2)
                    .frame(maxWidth: 120, alignment: .trailing)
            }
        }
        .task(id: image.word) {
            guard let lex = model.lexicon else { return }
            let w = image.word
            let data = await Task.detached { lex.imageBlob("en", w) }.value
            ui = data.flatMap(UIImage.init(data:))
        }
    }
}

private struct TranslationSectionView: View {
    let section: TranslationSection

    var body: some View {
        SectionHeader(title: section.title)
        if let note = section.note {
            Note(note).padding(.bottom, 6)
        }
        VStack(alignment: .leading, spacing: 10) {
            ForEach(section.items, id: \.self) { item in
                VStack(alignment: .leading, spacing: 2) {
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        WordLinkView(word: item.word, lang: item.lang)
                        if let r = item.reading {
                            Text(r).foregroundStyle(Theme.accent).font(.subheadline)
                        }
                    }
                    if !item.glosses.isEmpty {
                        Text(item.glosses.joined(separator: "; "))
                            .font(.subheadline).foregroundStyle(Theme.muted)
                    }
                    if !item.via.isEmpty {
                        Text("← " + item.via.joined(separator: ", "))
                            .font(.caption).foregroundStyle(Theme.faint)
                            .accessibilityLabel("through English " + item.via.joined(separator: ", "))
                    }
                }
            }
        }
    }
}

private struct ExampleSectionView: View {
    let section: ExampleSection

    var body: some View {
        SectionHeader(title: section.title)
        VStack(alignment: .leading, spacing: 12) {
            ForEach(section.lines, id: \.self) { line in
                VStack(alignment: .leading, spacing: 2) {
                    line.segments.reduce(Text("")) { $0 + Text($1.text).bold($1.bold) }
                        .textSelection(.enabled)
                    if let t = line.translation {
                        Text(t).italic().foregroundStyle(Theme.muted).font(.subheadline)
                    }
                }
                .ruled()
            }
        }
    }
}

private extension View {
    /// The thin left rule the desktop draws beside examples and etymologies.
    func ruled() -> some View {
        padding(.leading, 13)
            .overlay(alignment: .leading) {
                Rectangle().fill(Theme.line).frame(width: 2)
            }
    }
}
