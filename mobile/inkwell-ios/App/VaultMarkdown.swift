import SwiftUI

/// Obsidian-style markdown rendering for read-only notes (2026-10-22 inkwell
/// note #2: "render markdown generally the same way obsidian does"). Block
/// level is parsed here line by line (headings, bullets/numbered/task lists
/// with nesting, quotes and callouts, code fences, rules, tables as
/// monospaced rows); inline styling (bold, italic, `code`, links,
/// ~~strike~~) goes through Foundation's markdown parser, with [[wikilinks]]
/// and ==highlights== rewritten first since CommonMark has neither.
struct MarkdownView: View {
    let text: String

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(Array(MarkdownView.blocks(text).enumerated()), id: \.offset) { _, b in
                block(b)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    enum Block {
        case heading(Int, String)
        case item(indent: Int, marker: String, text: String, checked: Bool?)
        case quote(String)
        case code(String)
        case rule
        case blank
        case para(String)
    }

    static func blocks(_ text: String) -> [Block] {
        var out: [Block] = []
        var code: [String]? = nil
        for raw in text.components(separatedBy: "\n") {
            let line = raw.replacingOccurrences(of: "\t", with: "    ")
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed.hasPrefix("```") {
                if let c = code { out.append(.code(c.joined(separator: "\n"))); code = nil } else { code = [] }
                continue
            }
            if code != nil { code!.append(line); continue }
            if trimmed.isEmpty { if case .blank? = out.last {} else { out.append(.blank) }; continue }
            if ["---", "***", "___"].contains(trimmed) { out.append(.rule); continue }
            if let m = trimmed.range(of: #"^#{1,6} "#, options: .regularExpression) {
                out.append(.heading(trimmed[m].count - 1, String(trimmed[m.upperBound...]))); continue
            }
            if trimmed.hasPrefix(">") {
                out.append(.quote(String(trimmed.drop(while: { $0 == ">" || $0 == " " })))); continue
            }
            let indent = (line.prefix(while: { $0 == " " }).count) / 2
            if let m = trimmed.range(of: #"^[-*+] \[( |x|X)\] "#, options: .regularExpression) {
                let checked = !trimmed[m].contains("[ ]")
                out.append(.item(indent: indent, marker: checked ? "☑" : "☐", text: String(trimmed[m.upperBound...]), checked: checked))
                continue
            }
            if let m = trimmed.range(of: #"^[-*+] "#, options: .regularExpression) {
                out.append(.item(indent: indent, marker: indent % 2 == 0 ? "•" : "◦", text: String(trimmed[m.upperBound...]), checked: nil))
                continue
            }
            if let m = trimmed.range(of: #"^\d+[.)] "#, options: .regularExpression) {
                out.append(.item(indent: indent, marker: String(trimmed[m]).trimmingCharacters(in: .whitespaces),
                                 text: String(trimmed[m.upperBound...]), checked: nil))
                continue
            }
            out.append(.para(line))
        }
        if let c = code { out.append(.code(c.joined(separator: "\n"))) }
        return out
    }

    @ViewBuilder private func block(_ b: Block) -> some View {
        switch b {
        case .heading(let level, let s):
            Text(MarkdownView.inline(s))
                .font(.system(level <= 1 ? .title2 : level == 2 ? .title3 : .headline, design: .monospaced).bold())
                .foregroundStyle(Theme.fg).padding(.top, level <= 2 ? 8 : 4)
        case .item(let indent, let marker, let s, let checked):
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(marker).foregroundStyle(checked == true ? Theme.accent : Theme.dim)
                Text(MarkdownView.inline(s))
                    .strikethrough(checked == true, color: Theme.dim)
                    .foregroundStyle(checked == true ? Theme.dim : Theme.fg)
            }
            .font(Theme.mono).padding(.leading, CGFloat(indent) * 18)
        case .quote(let s):
            HStack(spacing: 8) {
                Rectangle().fill(Theme.accent.opacity(0.6)).frame(width: 3)
                Text(MarkdownView.inline(s)).font(Theme.mono).foregroundStyle(Theme.dim)
            }
            .fixedSize(horizontal: false, vertical: true)
        case .code(let s):
            ScrollView(.horizontal, showsIndicators: false) {
                Text(s).font(Theme.monoSmall).foregroundStyle(Theme.fg).padding(8)
            }
            .background(Color(white: 0.1)).clipShape(RoundedRectangle(cornerRadius: 6))
        case .rule:
            Divider().overlay(Color(white: 0.25)).padding(.vertical, 4)
        case .blank:
            Color.clear.frame(height: 4)
        case .para(let s):
            Text(MarkdownView.inline(s)).font(Theme.mono).foregroundStyle(Theme.fg)
        }
    }

    /// Inline markdown → AttributedString. Wikilinks render as accent text
    /// (the alias if present); ==x== as highlighted; a parse failure falls
    /// back to the raw line so nothing is ever lost.
    static func inline(_ s: String) -> AttributedString {
        var t = s.replacingOccurrences(of: #"\[\[([^\]|]+)\|([^\]]+)\]\]"#, with: "**$2**", options: .regularExpression)
        t = t.replacingOccurrences(of: #"\[\[([^\]]+)\]\]"#, with: "**$1**", options: .regularExpression)
        t = t.replacingOccurrences(of: #"==([^=]+)=="#, with: "***$1***", options: .regularExpression)
        let opts = AttributedString.MarkdownParsingOptions(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        var a = (try? AttributedString(markdown: t, options: opts)) ?? AttributedString(s)
        for run in a.runs where run.link != nil { a[run.range].foregroundColor = Theme.accent }
        return a
    }
}
