import SwiftUI
import InkwellCore

/// Obsidian-style markdown rendering for read-only notes (2026-10-22 inkwell
/// note #2: "render markdown generally the same way obsidian does"). Block
/// level is parsed here line by line (headings, bullets/numbered/task lists
/// with nesting, quotes and callouts, code fences, rules, pipe tables as a
/// grid); headings step down h1 > h2 > h3 > h4 > body; inline styling (bold, italic, `code`, links,
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
        case table(header: [String]?, rows: [[String]], align: [MarkdownLine.Align])
        case blank
        case para(String)
    }

    static func blocks(_ text: String) -> [Block] {
        var out: [Block] = []
        var code: [String]? = nil
        var table: [String] = []
        func flushTable() {
            guard !table.isEmpty else { return }
            let sep = table.count > 1 && MarkdownLine.isTableSeparator(table[1])
            let rows = table.enumerated().filter { !(sep && $0.offset == 1) }.map { MarkdownLine.tableCells($0.element) }
            out.append(.table(header: sep ? rows[0] : nil, rows: sep ? Array(rows.dropFirst()) : rows,
                              align: sep ? MarkdownLine.alignments(table[1]) : []))
            table = []
        }
        for raw in text.components(separatedBy: "\n") {
            let line = raw.replacingOccurrences(of: "\t", with: "    ")
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if code == nil, MarkdownLine.isTableRow(line) { table.append(line); continue }
            flushTable()
            if trimmed.hasPrefix("```") {
                if let c = code { out.append(.code(c.joined(separator: "\n"))); code = nil } else { code = [] }
                continue
            }
            if code != nil { code!.append(line); continue }
            if trimmed.isEmpty { if case .blank? = out.last {} else { out.append(.blank) }; continue }
            if ["---", "***", "___"].contains(trimmed) { out.append(.rule); continue }
            if let level = MarkdownLine.headingLevel(trimmed) {
                out.append(.heading(level, String(trimmed.dropFirst(level)).trimmingCharacters(in: .whitespaces))); continue
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
        flushTable()
        if let c = code { out.append(.code(c.joined(separator: "\n"))) }
        return out
    }

    @ViewBuilder private func block(_ b: Block) -> some View {
        switch b {
        case .heading(let level, let s):
            Text(MarkdownView.inline(s))
                .font(.system(MarkdownView.headingStyle(level), design: .monospaced).bold())
                .foregroundStyle(Theme.fg).padding(.top, level <= 2 ? 10 : 4)
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
        case .table(let header, let rows, let align):
            MarkdownTable(header: header, rows: rows, align: align)
        case .rule:
            Divider().overlay(Color(white: 0.25)).padding(.vertical, 4)
        case .blank:
            Color.clear.frame(height: 4)
        case .para(let s):
            Text(MarkdownView.inline(s)).font(Theme.mono).foregroundStyle(Theme.fg)
        }
    }

    /// h1 .title (28) > h2 .title2 (22) > h3 .title3 (20) > h4+ .headline
    /// (17 bold) > body (17), all scaling with Dynamic Type.
    static func headingStyle(_ level: Int) -> Font.TextStyle {
        [.title, .title2, .title3][safe: level - 1] ?? .headline
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

/// Pipe table as a grid: bold header over a rule, hairlines between rows,
/// columns sized to their widest cell and aligned per the separator row.
/// Scrolls sideways when it outgrows the screen instead of wrapping cells
/// into ribbons.
private struct MarkdownTable: View {
    let header: [String]?
    let rows: [[String]]
    let align: [MarkdownLine.Align]

    private var columns: Int { ([header?.count ?? 0] + rows.map(\.count)).max() ?? 0 }

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 6) {
                if let header {
                    row(header).font(Theme.mono.bold())
                    Rectangle().fill(Color(white: 0.3)).frame(height: 1).gridCellUnsizedAxes(.horizontal)
                }
                ForEach(Array(rows.enumerated()), id: \.offset) { i, r in
                    if i > 0 { Rectangle().fill(Color(white: 0.16)).frame(height: 1).gridCellUnsizedAxes(.horizontal) }
                    row(r).font(Theme.mono)
                }
            }
            .padding(10)
            .background(Color(white: 0.07)).clipShape(RoundedRectangle(cornerRadius: 6))
        }
    }

    private func row(_ cells: [String]) -> some View {
        GridRow {
            ForEach(0..<columns, id: \.self) { c in
                Text(MarkdownView.inline(cells[safe: c] ?? ""))
                    .foregroundStyle(Theme.fg)
                    .gridColumnAlignment(alignment(c))
            }
        }
    }

    private func alignment(_ c: Int) -> HorizontalAlignment {
        switch align[safe: c] ?? .leading {
        case .leading: return .leading
        case .center: return .center
        case .trailing: return .trailing
        }
    }
}

private extension Array {
    subscript(safe i: Int) -> Element? { indices.contains(i) ? self[i] : nil }
}
