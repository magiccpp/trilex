import SwiftUI
import TrilexKit
import UIKit

/// The desktop palette (trilex/theme.py): birch and linen for light, iron
/// and charcoal for dark, and a muted fjord blue as the single accent.
enum Theme {
    static let accent = Color.accentColor
    static let background = dynamic(0xF7F5F0, 0x191817)
    static let muted = dynamic(0x6D6459, 0x9B9285)
    static let faint = dynamic(0x9B9285, 0x71695F)
    static let line = dynamic(0xE3DED4, 0x312D28)
    static let chip = dynamic(0xEEEAE1, 0x2A2723)

    private static func dynamic(_ light: UInt32, _ dark: UInt32) -> Color {
        Color(uiColor: UIColor { traits in
            UIColor(rgb: traits.userInterfaceStyle == .dark ? dark : light)
        })
    }

    /// Chinese headwords read better a size up.
    static func wordFont(_ word: String, base: CGFloat) -> Font {
        .system(size: Norm.isCJK(word) ? base * 1.25 : base, weight: .semibold)
    }
}

extension UIColor {
    convenience init(rgb: UInt32) {
        self.init(red: CGFloat((rgb >> 16) & 0xFF) / 255, green: CGFloat((rgb >> 8) & 0xFF) / 255,
                  blue: CGFloat(rgb & 0xFF) / 255, alpha: 1)
    }
}

/// The section rule: a small lozenge and a spaced caps title.
struct SectionHeader: View {
    let title: String

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Text("◆").font(.system(size: 9)).foregroundStyle(Theme.accent)
                Text(title.uppercased())
                    .font(.footnote.weight(.bold))
                    .tracking(1.4)
                    .foregroundStyle(Theme.muted)
            }
            Rectangle().fill(Theme.line).frame(height: 1)
        }
        .padding(.top, 26)
        .padding(.bottom, 8)
        .accessibilityAddTraits(.isHeader)
    }
}

struct Note: View {
    let text: String
    init(_ text: String) { self.text = text }

    var body: some View {
        Text(text).italic().foregroundStyle(Theme.muted).font(.subheadline)
    }
}

struct Chip: View {
    let text: Text
    init(_ text: Text) { self.text = text }

    var body: some View {
        text
            .foregroundStyle(Theme.muted)
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.chip, in: RoundedRectangle(cornerRadius: 8))
    }
}

struct LangBadge: View {
    let lang: String

    var body: some View {
        Text(LANG_NAME[lang] ?? lang)
            .font(.caption)
            .tracking(0.8)
            .foregroundStyle(Theme.faint)
    }
}

/// A word that opens its own entry, the way q: links do on the desktop.
struct WordLinkView: View {
    let word: String
    let lang: String
    var size: CGFloat = 17

    var body: some View {
        NavigationLink(value: WordLink(word: word, lang: lang)) {
            Text(word).font(Theme.wordFont(word, base: size)).foregroundStyle(Theme.accent)
        }
        .buttonStyle(.plain)
    }
}

extension View {
    /// Every navigation stack can open a word from a link inside a page.
    func wordDestinations() -> some View {
        navigationDestination(for: WordLink.self) { link in
            ResultView(query: link.word)
        }
    }
}
