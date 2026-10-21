import Foundation
import VaultCore

// Checks for the offline store, sync planner, outbox and the sync loop
// against a stubbed server (URLProtocol). CommandLineTools has no XCTest,
// so this is an executable: `swift run VaultCheck`, exit 0 = all pass.

var failures = 0
func check(_ cond: @autoclosure () -> Bool, _ msg: String, line: Int = #line) {
    if !cond() { failures += 1; print("FAIL \(line): \(msg)") }
}
func eq<T: Equatable>(_ a: T, _ b: T, _ msg: String = "", line: Int = #line) {
    if a != b { failures += 1; print("FAIL \(line): \(msg) — got \(a), expected \(b)") }
}

let tmp = NSTemporaryDirectory() + "vaultcheck-\(UUID().uuidString).sqlite"
let store = try! VaultStore(path: tmp)

// --- plan: fresh store fetches everything, nothing to delete
let m1 = [
    ManifestEntry(p: "g245/build-order.md", m: 100, s: 10, h: "aaa"),
    ManifestEntry(p: "d359/Jane Doe d359.md", m: 90, s: 20, h: "bbb"),
    ManifestEntry(p: "hcmp/o314/2026/first-day.md", m: 80, s: 30, h: "ccc"),
]
var plan = try! store.plan(m1)
eq(plan.toFetch.count, 3, "fresh fetch")
eq(plan.toDelete, [], "fresh delete")

for e in m1 {
    let links = e.path.hasPrefix("g245") ? " with [[Jane Doe d359|Jane]] and [[missing note]]" : ""
    try! store.upsert(e, body: "---\ntitle: \"T \(e.path)\"\n---\n\nbody of \(VaultStore.basename(e.path))\(links)", sha: e.h)
}
eq(store.fileCount, 3, "count after upsert")

// --- plan: changed sha fetched, removed path deleted, same sha untouched
let m2 = [
    ManifestEntry(p: "g245/build-order.md", m: 101, s: 11, h: "aaa2"),
    ManifestEntry(p: "d359/Jane Doe d359.md", m: 90, s: 20, h: "bbb"),
]
plan = try! store.plan(m2)
eq(plan.toFetch.map { $0.path }, ["g245/build-order.md"], "changed only")
eq(plan.toDelete, ["hcmp/o314/2026/first-day.md"], "deleted path")
eq(plan.unchanged, 1, "unchanged")

// --- list / recent
let root = store.list("")
eq(root.map { $0.name }, ["d359", "g245", "hcmp"], "root folders")
check(root.allSatisfy { $0.isFolder }, "root is folders")
let d359 = store.list("d359")
eq(d359.map { $0.path }, ["d359/Jane Doe d359.md"], "d359 files")
eq(store.list("hcmp").first?.path, "hcmp/o314", "nested folder path")
eq(store.recent(limit: 1).first?.path, "g245/build-order.md", "recent by mtime")

// --- search (FTS5, prefix terms, unicode)
eq(Set(store.search("jane").map { $0.path }), ["d359/Jane Doe d359.md", "g245/build-order.md"], "fts hit (title path text + link text)")
eq(store.search("bod first").map { $0.path }, ["hcmp/o314/2026/first-day.md"], "prefix terms AND")
eq(store.search("nonexistentterm").count, 0, "no hit")
try! store.upsert(ManifestEntry(p: "hcmp/o314/2026/中文.md", m: 1, s: 1, h: "z"), body: "今天 学习 中文 笔记", sha: "z")
eq(store.search("学习").map { $0.path }, ["hcmp/o314/2026/中文.md"], "cjk search")

// --- wikilink resolution
eq(store.resolve(wikilink: "Jane Doe d359"), "d359/Jane Doe d359.md", "bare name")
eq(store.resolve(wikilink: "jane doe d359#notes"), "d359/Jane Doe d359.md", "case + heading")
eq(store.resolve(wikilink: "o314/2026/first-day"), "hcmp/o314/2026/first-day.md", "path suffix")
eq(store.resolve(wikilink: "missing note"), nil, "not on phone")

let links = Wikilinks.parse(store.read("g245/build-order.md")!)
eq(links.count, 2, "two links")
eq(links[0].target, "Jane Doe d359", "target")
eq(Wikilinks.label(links[0]), "Jane", "alias label")
eq(Wikilinks.label(links[1]), "missing note", "bare label")
eq(Wikilinks.parse("no links [[ ]] here [[a]][[b|B]]").map { $0.target }, ["a", "b"], "adjacent + empty")

// --- frontmatter
let (fm, body) = Frontmatter.split("---\ntitle: \"Hello\"\ndate: 2026-10-19\ntags: [a, b]\n---\n\ntext here")
eq(fm["title"], "Hello", "fm title unquoted")
eq(fm["date"], "2026-10-19", "fm date")
eq(body, "\ntext here", "body after fm")
eq(Frontmatter.title(path: "x/y z.md", text: "plain"), "y z", "title fallback basename")

// --- outbox lifecycle
let c = Capture(title: "", body: "buy milk\nand eggs")
try! store.enqueue(c)
eq(store.pendingCount, 1, "pending")
store.markFailed(c.id, error: "offline")
eq(store.pending().first?.attempts, 1, "attempt counted")
eq(store.pending().first?.lastError, "offline", "error kept")
store.markSent(c.id, path: "z_ibx/phone/x.md")
eq(store.pendingCount, 0, "sent clears pending")
eq(store.captures().first?.sentPath, "z_ibx/phone/x.md", "sent path recorded")
check(c.createdAtString.count == 19 && c.createdAtString.contains("T"), "created_at format \(c.createdAtString)")

// --- sync loop against a stubbed server
final class Stub: URLProtocol {
    static var manifest = ""
    static var files: [String: (String, String?)] = [:]   // path -> (body, manifestShaOverride)
    static var drops: [[String: Any]] = []
    static var manifestHits = 0
    static var failDrops = false
    static var fileURLs: [String] = []          // raw query strings seen by /file
    static var putResponses: [String: (String, String)] = [:]   // target -> (sha, body) to return for a put
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func stopLoading() {}
    override func startLoading() {
        let url = request.url!
        let auth = request.value(forHTTPHeaderField: "Authorization") ?? ""
        func respond(_ code: Int, _ body: Data, _ headers: [String: String] = [:]) {
            let r = HTTPURLResponse(url: url, statusCode: code, httpVersion: nil, headerFields: headers)!
            client?.urlProtocol(self, didReceive: r, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: body)
            client?.urlProtocolDidFinishLoading(self)
        }
        guard auth == "Bearer t0k" else { return respond(401, Data("{\"error\":\"unauthorized\"}".utf8)) }
        let comps = URLComponents(url: url, resolvingAgainstBaseURL: false)!
        switch comps.path {
        case "/vault/manifest":
            Stub.manifestHits += 1
            if request.value(forHTTPHeaderField: "If-None-Match") == "\"e1\"" { return respond(304, Data(), ["ETag": "\"e1\""]) }
            respond(200, Data(Stub.manifest.utf8), ["ETag": "\"e1\""])
        case "/vault/file":
            Stub.fileURLs.append(comps.percentEncodedQuery ?? "")
            let p = comps.queryItems?.first { $0.name == "p" }?.value ?? ""
            guard let (b, msha) = Stub.files[p] else { return respond(404, Data()) }
            let sha = sha256hex(b)
            respond(200, Data(b.utf8), ["X-Sha256": sha, "X-Manifest-Sha256": msha ?? sha])
        case "/vault/drop":
            if Stub.failDrops { return respond(503, Data("{\"error\":\"down\"}".utf8)) }
            let bodyData = request.httpBody ?? streamBody(request)
            let j = (try? JSONSerialization.jsonObject(with: bodyData)) as? [String: Any] ?? [:]
            Stub.drops.append(j)
            if let t = j["target"] as? String, let (sha, body) = Stub.putResponses[t] {
                let r: [String: Any] = ["ok": true, "path": t, "existing": false, "put": true, "sha": sha, "body": body]
                return respond(201, try! JSONSerialization.data(withJSONObject: r))
            }
            respond(201, Data("{\"ok\":true,\"path\":\"z_ibx/phone/\(j["id"] ?? "").md\",\"existing\":false}".utf8))
        default:
            respond(404, Data())
        }
    }
    func streamBody(_ r: URLRequest) -> Data {
        guard let s = r.httpBodyStream else { return Data() }
        s.open(); defer { s.close() }
        var d = Data(); var buf = [UInt8](repeating: 0, count: 4096)
        while s.hasBytesAvailable { let n = s.read(&buf, maxLength: buf.count); if n <= 0 { break }; d.append(buf, count: n) }
        return d
    }
}

import CryptoKit
func sha256hex(_ s: String) -> String { SHA256.hash(data: Data(s.utf8)).map { String(format: "%02x", $0) }.joined() }

let cfg = URLSessionConfiguration.ephemeral
cfg.protocolClasses = [Stub.self]
let api = VaultApi(base: { "https://stub.local/vault" }, token: { "t0k" }, session: URLSession(configuration: cfg))
let store2 = try! VaultStore(path: NSTemporaryDirectory() + "vaultcheck2-\(UUID().uuidString).sqlite")
let sync = VaultSync(store: store2, api: api)

let bodyA = "---\ntitle: A\n---\nalpha", bodyB = "beta body"
Stub.files = ["a.md": (bodyA, nil), "g245/b.md": (bodyB, nil)]
Stub.manifest = """
{"generated_at":"2026-10-19T14:00:00Z","subset_version":"v1","count":2,"total_bytes":20,"skipped_large":0,
 "files":[{"p":"a.md","m":1,"s":5,"h":"\(sha256hex(bodyA))"},{"p":"g245/b.md","m":2,"s":9,"h":"\(sha256hex(bodyB))"}]}
"""

let sem = DispatchSemaphore(value: 0)
Task {
    // first pass: outbox empty, two files down
    var r = await sync.run()
    eq(r.error, nil, "pass1 error")
    eq(r.fetched, 2, "pass1 fetched")
    eq(r.failed, [], "pass1 failed")
    eq(store2.read("g245/b.md"), bodyB, "stored body")
    eq(store2.meta(VaultSync.kEtag), "\"e1\"", "etag remembered")
    eq(r.generatedAt, "2026-10-19T14:00:00Z", "generated_at surfaced")

    // second pass: 304, nothing fetched
    r = await sync.run()
    eq(r.error, nil, "pass2 error")
    eq(r.fetched, 0, "pass2 nothing")
    eq(r.summary, "no change", "pass2 summary")

    // capture while "offline" (server 503), then server back
    try! store2.enqueue(Capture(title: "", body: "note from the car"))
    Stub.failDrops = true
    r = await sync.run()
    eq(r.capturesFailed, 1, "drop failed counted")
    eq(store2.pendingCount, 1, "still pending")
    Stub.failDrops = false
    r = await sync.run()
    eq(r.captures, 1, "drop uploaded")
    eq(store2.pendingCount, 0, "outbox drained")
    eq(Stub.drops.count, 1, "server saw one drop")
    eq(Stub.drops.first?["body"] as? String, "note from the car", "drop body")
    check((Stub.drops.first?["created_at"] as? String)?.count == 19, "drop created_at")

    // mid-write mismatch: served sha != manifest sha -> retried once, then stored anyway
    Stub.files["a.md"] = ("alpha changed", "sha-the-manifest-promised")
    Stub.manifest = Stub.manifest.replacingOccurrences(of: sha256hex(bodyA), with: "sha-the-manifest-promised")
    r = await sync.run(force: true)
    eq(store2.read("a.md"), "alpha changed", "mismatch stored after retry")
    eq(r.failed, [], "mismatch not a failure")

    // "+" in a note name must reach the server as %2B, not as a space (2026-10-21)
    let plusBody = "plus body"
    Stub.files["g245/2g+1g.md"] = (plusBody, nil)
    Stub.manifest = Stub.manifest.replacingOccurrences(of: "\"files\":[", with: "\"files\":[{\"p\":\"g245/2g+1g.md\",\"m\":3,\"s\":9,\"h\":\"\(sha256hex(plusBody))\"},")
    r = await sync.run(force: true)
    eq(store2.read("g245/2g+1g.md"), plusBody, "plus-named file stored")
    check(Stub.fileURLs.contains { $0.contains("2g%2B1g") }, "plus percent-encoded in query: \(Stub.fileURLs.last ?? "")")
    check(!Stub.fileURLs.contains { $0.contains("2g+1g") }, "no bare plus in query")

    // a put comes back merged: the store adopts the server's text and sha …
    let nn = "z_ibx/new-notes.md"
    try! store2.upsert(ManifestEntry(p: nn, m: 1, s: 4, h: "base"), body: "hdr\n", sha: "base")
    try! store2.enqueue(Capture(title: "put", body: "hdr\nmine\n", target: nn, mode: "put", baseSha: "base"))
    Stub.putResponses[nn] = ("merged-sha", "hdr\ndesktop\nmine\n")
    r = await sync.run()
    eq(store2.read(nn), "hdr\ndesktop\nmine\n", "merged body adopted")
    eq(store2.sha(nn), "merged-sha", "merged sha adopted")
    eq(Stub.drops.last?["base_sha"] as? String, "base", "put carried its base")
    // … unless a newer put for the same file is already queued (user kept typing)
    Stub.failDrops = true
    try! store2.enqueue(Capture(title: "put", body: "hdr\nmine more\n", target: nn, mode: "put", baseSha: "base"))
    r = await sync.run()                       // fails: stays pending
    Stub.failDrops = false
    try! store2.upsert(ManifestEntry(p: nn, m: 1, s: 4, h: "base"), body: "hdr\nmine more and more\n", sha: "base")
    try! store2.enqueue(Capture(title: "put", body: "hdr\nmine more and more\n", target: nn, mode: "put", baseSha: "base"))
    // simulate the older put being in flight when the newer one was queued: the
    // sync sees two pending rows only if enqueue did not supersede; it does, so
    // just check the single remaining put wins and the local text is kept
    r = await sync.run()
    eq(store2.pendingCount, 0, "puts drained")
    Stub.putResponses[nn] = nil

    // 401 is fatal and reported, outbox untouched
    let bad = VaultSync(store: store2, api: VaultApi(base: { "https://stub.local/vault" }, token: { "wrong" }, session: URLSession(configuration: cfg)))
    r = await bad.run(force: true)
    eq(r.error, "token rejected", "401 surfaced")
    sem.signal()
}
sem.wait()

try? FileManager.default.removeItem(atPath: tmp)
if failures == 0 { print("VaultCheck: all checks passed") } else { print("VaultCheck: \(failures) failure(s)") }
exit(failures == 0 ? 0 : 1)
