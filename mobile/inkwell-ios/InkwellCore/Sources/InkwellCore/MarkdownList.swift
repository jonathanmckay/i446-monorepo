import Foundation

/// Obsidian-style markdown list autofill. Pure Foundation so it can be unit
/// tested from CoreCheck and reused by any editor surface. The UITextView in
/// the note editor calls `onNewline` from its delegate's `shouldChangeTextIn`.
///
/// Rules (indentation always preserved):
///   - `- ` / `* ` / `+ `   → next line repeats the same bullet
///   - `1. ` / `1) `        → next line increments the number, keeps the delimiter
///   - `- [ ] ` / `- [x] `  → next line is a fresh unchecked `- [ ] `
///   - an *empty* item (only the marker, no content) → pressing return strips
///     the marker and breaks out of the list (blank line, no new marker)
public enum MarkdownList {

    /// A concrete edit for the editor to apply: replace `range` in the current
    /// text with `replacement`, then place the caret at `cursor`. All offsets
    /// are UTF-16 (NSString) units so they map straight onto UITextView.
    public struct Edit: Equatable {
        public let range: NSRange
        public let replacement: String
        public let cursor: Int
        public init(range: NSRange, replacement: String, cursor: Int) {
            self.range = range
            self.replacement = replacement
            self.cursor = cursor
        }
    }

    /// Decide what a return keypress should do. `text` is the full buffer,
    /// `selection` the range about to be replaced by the newline (caret =
    /// zero-length). Returns nil when the caret is not on a list item, letting
    /// the editor insert a plain newline.
    public static func onNewline(text: NSString, selection: NSRange) -> Edit? {
        guard selection.length == 0 else { return nil }
        let loc = selection.location
        guard loc <= text.length else { return nil }

        let lineRange = text.lineRange(for: NSRange(location: loc, length: 0))
        let prefix = text.substring(with: NSRange(location: lineRange.location,
                                                  length: loc - lineRange.location))
        guard let marker = parse(prefix) else { return nil }

        if marker.bodyEmpty {
            // Break out: strip the marker (indent + bullet) from the line, no
            // new line. Caret ends where the line began.
            let clear = NSRange(location: lineRange.location, length: loc - lineRange.location)
            return Edit(range: clear, replacement: "", cursor: lineRange.location)
        } else {
            let insert = "\n" + marker.indent + marker.next
            let cursor = loc + (insert as NSString).length
            return Edit(range: selection, replacement: insert, cursor: cursor)
        }
    }

    // MARK: - Pure parsing

    /// The list structure of a single line prefix (line start → caret).
    struct Marker: Equatable {
        /// Leading whitespace to carry onto the continuation line.
        let indent: String
        /// Marker to write on the next line (without indent), e.g. "- ", "2. ".
        let next: String
        /// The current item has no content after its marker.
        let bodyEmpty: Bool
    }

    static func parse(_ line: String) -> Marker? {
        let indentCount = line.prefix { $0 == " " || $0 == "\t" }.count
        let indent = String(line.prefix(indentCount))
        let rest = String(line.dropFirst(indentCount))

        // Checkbox first — it's a superset of the plain bullet.
        if let g = match(rest, #"^([-*+]) \[[ xX]\]\s(.*)$"#) {
            return Marker(indent: indent, next: "\(g[1]) [ ] ", bodyEmpty: isBlank(g[2]))
        }
        // Unordered bullet.
        if let g = match(rest, #"^([-*+])\s(.*)$"#) {
            return Marker(indent: indent, next: "\(g[1]) ", bodyEmpty: isBlank(g[2]))
        }
        // Ordered: number + "." or ")".
        if let g = match(rest, #"^(\d+)([.)])\s(.*)$"#) {
            let n = Int(g[1]) ?? 0
            return Marker(indent: indent, next: "\(n + 1)\(g[2]) ", bodyEmpty: isBlank(g[3]))
        }
        return nil
    }

    private static func isBlank(_ s: String) -> Bool {
        s.trimmingCharacters(in: .whitespaces).isEmpty
    }

    /// Returns the capture groups (index 0 = whole match) or nil.
    private static func match(_ s: String, _ pattern: String) -> [String]? {
        guard let re = try? NSRegularExpression(pattern: pattern) else { return nil }
        let ns = s as NSString
        guard let m = re.firstMatch(in: s, range: NSRange(location: 0, length: ns.length)) else { return nil }
        return (0..<m.numberOfRanges).map { i in
            let r = m.range(at: i)
            return r.location == NSNotFound ? "" : ns.substring(with: r)
        }
    }
}
