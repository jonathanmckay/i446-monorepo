import Foundation

/// One sync pass. Order matters: captures go up first (they are the thing
/// that must never be lost), then the manifest comes down, then changed
/// files, concurrently, with a sha check and one retry each. Every outcome
/// is reported, nothing is thrown: the UI shows the summary line.
public struct SyncReport: Equatable {
    public var captures: Int = 0        // uploaded this pass
    public var capturesFailed: Int = 0
    public var fetched: Int = 0
    public var deleted: Int = 0
    public var unchanged: Int = 0
    public var failed: [String] = []    // paths
    public var manifestChanged = false
    public var generatedAt: String?
    public var error: String?           // fatal for the pass (no manifest)

    public var summary: String {
        if let e = error { return e }
        var parts: [String] = []
        if captures > 0 { parts.append("\(captures)↑") }
        if capturesFailed > 0 { parts.append("\(capturesFailed) stuck") }
        if fetched > 0 { parts.append("\(fetched)↓") }
        if deleted > 0 { parts.append("\(deleted)−") }
        if !failed.isEmpty { parts.append("\(failed.count) failed") }
        if parts.isEmpty { return manifestChanged ? "up to date" : "no change" }
        return parts.joined(separator: " ")
    }
}

public final class VaultSync {
    public let store: VaultStore
    public let api: VaultApi
    public var concurrency = 6
    public var progress: ((Int, Int) -> Void)?   // (done, total) for fetches

    public static let kEtag = "manifest_etag", kGenerated = "manifest_generated_at", kLastSync = "last_sync", kSubset = "subset_version"

    public init(store: VaultStore, api: VaultApi) {
        self.store = store
        self.api = api
    }

    public func run(force: Bool = false) async -> SyncReport {
        var r = SyncReport()
        await flushOutbox(&r)

        let etag = force ? nil : store.meta(VaultSync.kEtag)
        switch await api.manifest(etag: etag) {
        case .error(let e):
            r.error = e
            return r
        case .notModified:
            r.generatedAt = store.meta(VaultSync.kGenerated)
            store.setMeta(VaultSync.kLastSync, VaultSync.now())
            return r
        case .ok(let m, let newEtag):
            r.manifestChanged = true
            r.generatedAt = m.generated_at
            guard var plan = try? store.plan(m.files) else { r.error = "local db error"; return r }
            r.unchanged = plan.unchanged
            // An unsent full-text edit must not be overwritten by the server's
            // older copy; it reconciles on the pass after the put lands.
            let held = store.pendingTargets()
            if !held.isEmpty { plan.toFetch.removeAll { held.contains($0.path) } }
            await fetchAll(plan.toFetch, &r)
            if (try? store.delete(plan.toDelete)) != nil { r.deleted = plan.toDelete.count }
            // Only remember the etag when every file landed; otherwise the next
            // pass re-reads the manifest and retries the stragglers.
            if r.failed.isEmpty {
                store.setMeta(VaultSync.kEtag, newEtag)
            } else {
                store.setMeta(VaultSync.kEtag, nil)
            }
            store.setMeta(VaultSync.kGenerated, m.generated_at)
            store.setMeta(VaultSync.kSubset, m.subset_version)
            store.setMeta(VaultSync.kLastSync, VaultSync.now())
            return r
        }
    }

    /// Upload pending captures oldest first. A 4xx is final (bad payload);
    /// anything else stays queued for the next pass. Runs even if the
    /// manifest step later fails.
    public func flushOutbox(_ r: inout SyncReport) async {
        for c in store.pending() {
            switch await api.drop(c) {
            case .ok(let path, _, let sha, let body):
                store.markSent(c.id, path: path)
                r.captures += 1
                // An in-place edit comes back as the server left it (merged if
                // the desktop had moved on): adopt it now rather than after the
                // next manifest pass. Not while a NEWER put for the same file
                // is still queued: that text supersedes this one.
                if let t = c.target, let sha = sha, let body = body, !store.pendingTargets().contains(t) {
                    let e = ManifestEntry(p: t, m: Int(Date().timeIntervalSince1970), s: body.utf8.count, h: sha)
                    try? store.upsert(e, body: body, sha: sha)
                }
            case .rejected(let why):
                store.markFailed(c.id, error: "rejected: \(why)")
                r.capturesFailed += 1
            case .error(let why):
                store.markFailed(c.id, error: why)
                r.capturesFailed += 1
                return   // transport is down; do not hammer the rest
            }
        }
    }

    private func fetchAll(_ entries: [ManifestEntry], _ r: inout SyncReport) async {
        guard !entries.isEmpty else { return }
        let total = entries.count
        var done = 0
        var failed: [String] = []
        var fetched = 0
        await withTaskGroup(of: (String, Bool).self) { group in
            var it = entries.makeIterator()
            func launch(_ e: ManifestEntry) {
                group.addTask { [api, store] in
                    (e.path, await VaultSync.fetchOne(e, api: api, store: store))
                }
            }
            for _ in 0..<concurrency { if let e = it.next() { launch(e) } }
            for await (path, ok) in group {
                done += 1
                if ok { fetched += 1 } else { failed.append(path) }
                progress?(done, total)
                if let e = it.next() { launch(e) }
            }
        }
        r.fetched = fetched
        r.failed = failed
    }

    /// Fetch, verify the served sha against the manifest's, retry once on a
    /// mismatch (an agent was mid-write), then store whatever the second
    /// read returned: the next manifest will reconcile it.
    static func fetchOne(_ e: ManifestEntry, api: VaultApi, store: VaultStore) async -> Bool {
        for attempt in 0..<2 {
            switch await api.file(e.path) {
            case .gone:
                return true   // the next manifest drops it; nothing to store
            case .error:
                if attempt == 1 { return false }
                try? await Task.sleep(nanoseconds: 300_000_000)
            case .ok(let f):
                let consistent = f.sha.isEmpty || f.manifestSha.isEmpty || f.sha == f.manifestSha
                if consistent || attempt == 1 {
                    return (try? store.upsert(e, body: f.body, sha: f.sha.isEmpty ? e.sha : f.sha)) != nil
                }
                try? await Task.sleep(nanoseconds: 500_000_000)
            }
        }
        return false
    }

    static func now() -> String { Capture.stamp.string(from: Date()) }
}

// MARK: - wikilinks

public enum Wikilinks {
    /// All `[[...]]` spans in a note body, in order.
    public static func parse(_ text: String) -> [Wikilink] {
        var out: [Wikilink] = []
        var search = text.startIndex
        while let open = text.range(of: "[[", range: search..<text.endIndex) {
            guard let close = text.range(of: "]]", range: open.upperBound..<text.endIndex) else { break }
            let inner = String(text[open.upperBound..<close.lowerBound])
            if !inner.contains("[[") && !inner.contains("\n") {
                let parts = inner.split(separator: "|", maxSplits: 1, omittingEmptySubsequences: false)
                let target = String(parts[0]).trimmingCharacters(in: .whitespaces)
                let alias = parts.count > 1 ? String(parts[1]).trimmingCharacters(in: .whitespaces) : nil
                if !target.isEmpty {
                    out.append(Wikilink(target: target, alias: alias, range: open.lowerBound..<close.upperBound))
                }
            }
            search = close.upperBound
        }
        return out
    }

    /// Display text for a link: alias, else the target without heading/path.
    public static func label(_ l: Wikilink) -> String {
        if let a = l.alias, !a.isEmpty { return a }
        var t = l.target
        if let h = t.firstIndex(of: "#") { t = String(t[..<h]) }
        return VaultStore.basename(t)
    }
}

// MARK: - frontmatter

public enum Frontmatter {
    /// Splits a note into (frontmatter dictionary, body). Values are kept as
    /// raw strings; enough for `title:` and `date:` in the list and header.
    public static func split(_ text: String) -> ([String: String], String) {
        guard text.hasPrefix("---\n") || text.hasPrefix("---\r\n") else { return ([:], text) }
        let afterOpen = text.index(text.startIndex, offsetBy: 4)
        guard let close = text.range(of: "\n---", range: afterOpen..<text.endIndex) else { return ([:], text) }
        let fm = String(text[afterOpen..<close.lowerBound])
        var bodyStart = close.upperBound
        if bodyStart < text.endIndex, text[bodyStart] == "\n" { bodyStart = text.index(after: bodyStart) }
        var dict: [String: String] = [:]
        for line in fm.split(separator: "\n") {
            guard let colon = line.firstIndex(of: ":"), !line.hasPrefix(" "), !line.hasPrefix("-") else { continue }
            let k = line[..<colon].trimmingCharacters(in: .whitespaces)
            var v = line[line.index(after: colon)...].trimmingCharacters(in: .whitespaces)
            if v.count >= 2, (v.hasPrefix("\"") && v.hasSuffix("\"")) || (v.hasPrefix("'") && v.hasSuffix("'")) { v = String(v.dropFirst().dropLast()) }
            dict[k] = v
        }
        return (dict, String(text[bodyStart...]))
    }

    public static func title(path: String, text: String) -> String {
        let (fm, _) = split(text)
        if let t = fm["title"], !t.isEmpty { return t }
        return VaultStore.basename(path)
    }
}
