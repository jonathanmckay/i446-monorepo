import Foundation

/// Line-level markdown classification shared by the read-only renderer
/// (MarkdownView) and the live-styled new-notes editor (2026-10-22: "titles
/// bigger, h1>h2>h3 > regular text, and tables"). Pure Foundation so
/// CoreCheck can test it.
public enum MarkdownLine {

    /// `# x` → 1 … `###### x` → 6; nil for anything else (`#tag`, `####### x`).
    public static func headingLevel(_ line: String) -> Int? {
        let t = line.drop(while: { $0 == " " })
        let hashes = t.prefix(while: { $0 == "#" }).count
        guard (1...6).contains(hashes) else { return nil }
        let rest = t.dropFirst(hashes)
        return rest.isEmpty || rest.first == " " ? hashes : nil
    }

    /// A pipe-table row: starts with `|` once trimmed and has at least one
    /// more pipe. Obsidian also accepts rows without the leading pipe, but
    /// requiring it keeps prose like "a | b" from turning into a table.
    public static func isTableRow(_ line: String) -> Bool {
        let t = line.trimmingCharacters(in: .whitespaces)
        return t.hasPrefix("|") && t.dropFirst().contains("|")
    }

    /// The `|---|:--:|` row under a table header.
    public static func isTableSeparator(_ line: String) -> Bool {
        guard isTableRow(line) else { return false }
        let cells = tableCells(line)
        return !cells.isEmpty && cells.allSatisfy {
            $0.range(of: #"^:?-+:?$"#, options: .regularExpression) != nil
        }
    }

    /// Cells of a row, trimmed, outer pipes dropped. `\|` stays a literal pipe.
    public static func tableCells(_ line: String) -> [String] {
        var t = line.trimmingCharacters(in: .whitespaces)
        if t.hasPrefix("|") { t.removeFirst() }
        if t.hasSuffix("|") && !t.hasSuffix("\\|") { t.removeLast() }
        var cells: [String] = [], cur = "", escaped = false
        for ch in t {
            if escaped { cur.append(ch); escaped = false; continue }
            if ch == "\\" { escaped = true; continue }
            if ch == "|" { cells.append(cur); cur = ""; continue }
            cur.append(ch)
        }
        if escaped { cur.append("\\") }
        cells.append(cur)
        return cells.map { $0.trimmingCharacters(in: .whitespaces) }
    }

    /// Column alignment from a separator row: `:--` leading, `:-:` center,
    /// `--:` trailing.
    public enum Align: Equatable { case leading, center, trailing }

    public static func alignments(_ separator: String) -> [Align] {
        tableCells(separator).map { c in
            switch (c.hasPrefix(":"), c.hasSuffix(":")) {
            case (true, true): return .center
            case (false, true): return .trailing
            default: return .leading
            }
        }
    }
}
