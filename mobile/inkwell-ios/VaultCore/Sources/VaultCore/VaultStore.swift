import Foundation

/// The phone's copy of the vault subset plus the capture outbox, in one
/// SQLite file. Reads are instant and offline; FTS5 answers search without
/// a network. All access is serialised on `queue`.
public final class VaultStore {
    public let db: VaultDB
    private let queue = DispatchQueue(label: "vault.store")

    public init(path: String) throws {
        db = try VaultDB(path: path)
        try db.exec("""
        CREATE TABLE IF NOT EXISTS files (
          path  TEXT PRIMARY KEY,
          mtime INTEGER NOT NULL,
          size  INTEGER NOT NULL,
          sha   TEXT NOT NULL,
          body  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS files_mtime ON files(mtime DESC);
        CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(path UNINDEXED, body, tokenize='unicode61 remove_diacritics 2');
        CREATE TABLE IF NOT EXISTS outbox (
          id         TEXT PRIMARY KEY,
          created_at REAL NOT NULL,
          title      TEXT NOT NULL DEFAULT '',
          body       TEXT NOT NULL,
          attempts   INTEGER NOT NULL DEFAULT 0,
          last_error TEXT,
          sent_path  TEXT
        );
        CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
        """)
        // 2026-10-20: append-in-place captures carry a target path. Older DBs
        // lack the column; ALTER fails harmlessly once it exists.
        try? db.exec("ALTER TABLE outbox ADD COLUMN target TEXT")
    }

    public static func defaultPath() -> String {
        let dir = (try? FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true))
            ?? URL(fileURLWithPath: NSTemporaryDirectory())
        return dir.appendingPathComponent("vault.sqlite").path
    }

    // MARK: meta

    public func meta(_ k: String) -> String? { queue.sync { try? db.scalarString("SELECT v FROM meta WHERE k=?", [k]) ?? nil } }
    public func setMeta(_ k: String, _ v: String?) {
        queue.sync {
            if let v = v { try? db.run("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", [k, v]) }
            else { try? db.run("DELETE FROM meta WHERE k=?", [k]) }
        }
    }

    // MARK: sync plan

    /// Full-manifest set diff against the local table.
    public func plan(_ manifest: [ManifestEntry]) throws -> SyncPlan {
        try queue.sync {
            let local = Dictionary(uniqueKeysWithValues: try db.query("SELECT path, sha FROM files") { ($0.string(0)!, $0.string(1)!) })
            var fetch: [ManifestEntry] = []
            var unchanged = 0
            var seen = Set<String>()
            for e in manifest {
                seen.insert(e.path)
                if local[e.path] == e.sha { unchanged += 1 } else { fetch.append(e) }
            }
            let del = local.keys.filter { !seen.contains($0) }.sorted()
            return SyncPlan(toFetch: fetch, toDelete: del, unchanged: unchanged)
        }
    }

    public func upsert(_ e: ManifestEntry, body: String, sha: String) throws {
        try queue.sync {
            try db.transaction {
                try db.run("INSERT INTO files(path,mtime,size,sha,body) VALUES(?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET mtime=excluded.mtime,size=excluded.size,sha=excluded.sha,body=excluded.body",
                           [e.path, e.m, e.s, sha, body])
                try db.run("DELETE FROM files_fts WHERE path=?", [e.path])
                try db.run("INSERT INTO files_fts(path, body) VALUES(?,?)", [e.path, body])
            }
        }
    }

    public func delete(_ paths: [String]) throws {
        guard !paths.isEmpty else { return }
        try queue.sync {
            try db.transaction {
                for p in paths {
                    try db.run("DELETE FROM files WHERE path=?", [p])
                    try db.run("DELETE FROM files_fts WHERE path=?", [p])
                }
            }
        }
    }

    // MARK: reads

    public var fileCount: Int { queue.sync { (try? db.scalarInt("SELECT count(*) FROM files")) ?? 0 } }

    public func read(_ path: String) -> String? {
        queue.sync { (try? db.scalarString("SELECT body FROM files WHERE path=?", [path])) ?? nil }
    }

    public func exists(_ path: String) -> Bool {
        queue.sync { ((try? db.scalarInt("SELECT 1 FROM files WHERE path=?", [path])) ?? 0) == 1 }
    }

    /// One level of the folder tree. `dir` is "" for the root.
    public func list(_ dir: String) -> [DirEntry] {
        let prefix = dir.isEmpty ? "" : dir + "/"
        let rows: [(String, Int)] = queue.sync {
            (try? db.query("SELECT path, mtime FROM files WHERE path LIKE ? ESCAPE '\\'", [VaultStore.likePrefix(prefix) + "%"]) { ($0.string(0)!, $0.int(1)) }) ?? []
        }
        var folders: [String: Int] = [:]
        var files: [DirEntry] = []
        for (p, m) in rows {
            let rest = String(p.dropFirst(prefix.count))
            if let slash = rest.firstIndex(of: "/") {
                let name = String(rest[..<slash])
                folders[name] = max(folders[name] ?? 0, m)
            } else {
                files.append(DirEntry(name: rest, path: p, isFolder: false, mtime: m))
            }
        }
        let f = folders.map { DirEntry(name: $0.key, path: prefix + $0.key, isFolder: true, mtime: $0.value) }
        return f.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
            + files.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
    }

    public func recent(limit: Int = 50) -> [DirEntry] {
        queue.sync {
            (try? db.query("SELECT path, mtime FROM files ORDER BY mtime DESC LIMIT ?", [limit]) {
                let p = $0.string(0)!
                return DirEntry(name: VaultStore.basename(p), path: p, isFolder: false, mtime: $0.int(1))
            }) ?? []
        }
    }

    /// FTS5 search. The query is tokenised into prefix terms so a half-typed
    /// word matches; quotes are stripped rather than passed to FTS syntax.
    public func search(_ raw: String, limit: Int = 40) -> [SearchHit] {
        let terms = raw.split(whereSeparator: { $0.isWhitespace }).map { $0.replacingOccurrences(of: "\"", with: "") }.filter { !$0.isEmpty }
        guard !terms.isEmpty else { return [] }
        let match = terms.map { "\"\($0)\"*" }.joined(separator: " ")
        return queue.sync {
            (try? db.query("SELECT path, snippet(files_fts, 1, '', '', '…', 12) FROM files_fts WHERE files_fts MATCH ? ORDER BY bm25(files_fts) LIMIT ?", [match, limit]) {
                SearchHit(path: $0.string(0)!, snippet: $0.string(1) ?? "")
            }) ?? []
        }
    }

    /// Obsidian-style resolution: a bare name matches any file whose basename
    /// (without .md) equals it, case-insensitively; a path-ish target matches
    /// by suffix. nil means "not on phone" and the UI renders a stub.
    public func resolve(wikilink target: String) -> String? {
        var t = target
        if let hash = t.firstIndex(of: "#") { t = String(t[..<hash]) }
        t = t.trimmingCharacters(in: .whitespaces)
        if t.isEmpty { return nil }
        let name = t.hasSuffix(".md") ? String(t.dropLast(3)) : t
        return queue.sync {
            if name.contains("/") {
                return (try? db.scalarString("SELECT path FROM files WHERE lower(path) = lower(?) OR lower(path) LIKE lower(?) ORDER BY length(path) LIMIT 1",
                                             [name + ".md", "%/" + VaultStore.likePrefix(name) + ".md"])) ?? nil
            }
            return (try? db.scalarString("SELECT path FROM files WHERE lower(path) = lower(?) OR lower(path) LIKE lower(?) ORDER BY length(path) LIMIT 1",
                                         [name + ".md", "%/" + VaultStore.likePrefix(name) + ".md"])) ?? nil
        }
    }

    // MARK: outbox

    public func enqueue(_ c: Capture) throws {
        try queue.sync {
            try db.run("INSERT INTO outbox(id,created_at,title,body,attempts,last_error,sent_path,target) VALUES(?,?,?,?,?,?,?,?)",
                       [c.id, c.createdAt.timeIntervalSince1970, c.title, c.body, c.attempts, c.lastError, c.sentPath, c.target])
        }
    }

    public func pending() -> [Capture] {
        queue.sync {
            (try? db.query("SELECT id,created_at,title,body,attempts,last_error,target FROM outbox WHERE sent_path IS NULL ORDER BY created_at") {
                Capture(id: $0.string(0)!, createdAt: Date(timeIntervalSince1970: $0.double(1)), title: $0.string(2) ?? "", body: $0.string(3) ?? "",
                        attempts: $0.int(4), lastError: $0.string(5), sentPath: nil, target: $0.string(6))
            }) ?? []
        }
    }

    public var pendingCount: Int { queue.sync { (try? db.scalarInt("SELECT count(*) FROM outbox WHERE sent_path IS NULL")) ?? 0 } }

    public func markSent(_ id: String, path: String) {
        queue.sync { try? db.run("UPDATE outbox SET sent_path=?, last_error=NULL WHERE id=?", [path, id]) }
    }

    public func markFailed(_ id: String, error: String) {
        queue.sync { try? db.run("UPDATE outbox SET attempts=attempts+1, last_error=? WHERE id=?", [error, id]) }
    }

    /// Everything captured on this phone, newest first, sent or not.
    public func captures(limit: Int = 100) -> [Capture] {
        queue.sync {
            (try? db.query("SELECT id,created_at,title,body,attempts,last_error,sent_path,target FROM outbox ORDER BY created_at DESC LIMIT ?", [limit]) {
                Capture(id: $0.string(0)!, createdAt: Date(timeIntervalSince1970: $0.double(1)), title: $0.string(2) ?? "", body: $0.string(3) ?? "",
                        attempts: $0.int(4), lastError: $0.string(5), sentPath: $0.string(6), target: $0.string(7))
            }) ?? []
        }
    }

    // MARK: helpers

    static func likePrefix(_ s: String) -> String {
        s.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "%", with: "\\%").replacingOccurrences(of: "_", with: "\\_")
    }
    public static func basename(_ p: String) -> String {
        let n = p.split(separator: "/").last.map(String.init) ?? p
        return n.hasSuffix(".md") ? String(n.dropLast(3)) : n
    }
}
