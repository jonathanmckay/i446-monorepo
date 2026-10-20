import Foundation

/// One line of the server manifest (`tools/vault-mobile/manifest.py`).
public struct ManifestEntry: Equatable, Codable {
    public let p: String   // vault-relative path
    public let m: Int      // mtime, seconds
    public let s: Int      // size, bytes
    public let h: String   // sha256 hex
    public init(p: String, m: Int, s: Int, h: String) { self.p = p; self.m = m; self.s = s; self.h = h }
    public var path: String { p }
    public var sha: String { h }
}

public struct Manifest: Codable {
    public let generated_at: String
    public let subset_version: String
    public let count: Int
    public let total_bytes: Int
    public let skipped_large: Int
    public let files: [ManifestEntry]
}

/// What a sync has to do, derived by set-diffing the manifest against the
/// local table. Deletions fall out of the full manifest; no tombstones.
public struct SyncPlan: Equatable {
    public var toFetch: [ManifestEntry]
    public var toDelete: [String]
    public var unchanged: Int
    public var isEmpty: Bool { toFetch.isEmpty && toDelete.isEmpty }
}

/// A note written on the phone. Never edits an existing vault file: the
/// server turns it into `z_ibx/phone/<stamp>-<id4>-<device>.md`.
public struct Capture: Equatable {
    public let id: String          // uuid, idempotency key on the server
    public let createdAt: Date
    public var title: String
    public var body: String
    public var attempts: Int
    public var lastError: String?
    public var sentPath: String?   // set once the server ACKs
    public var target: String?     // append in place to this vault file (allow-listed server-side) instead of a new capture file

    public init(id: String = UUID().uuidString.lowercased(), createdAt: Date = Date(), title: String = "", body: String,
                attempts: Int = 0, lastError: String? = nil, sentPath: String? = nil, target: String? = nil) {
        self.id = id; self.createdAt = createdAt; self.title = title; self.body = body
        self.attempts = attempts; self.lastError = lastError; self.sentPath = sentPath; self.target = target
    }

    /// ISO-8601 local wall time, no zone: the server files captures by the
    /// moment the thumb hit the key, in the zone it hit it.
    public var createdAtString: String { Capture.stamp.string(from: createdAt) }
    public static let stamp: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        return f
    }()
}

public struct DirEntry: Equatable, Hashable {
    public let name: String
    public let path: String       // folder: prefix without trailing slash; file: full path
    public let isFolder: Bool
    public let mtime: Int         // folders: max mtime inside
}

public struct SearchHit: Equatable {
    public let path: String
    public let snippet: String
}

/// `[[target|alias]]` / `[[target#heading]]` as Obsidian writes them.
public struct Wikilink: Equatable {
    public let target: String
    public let alias: String?
    public let range: Range<String.Index>
}
