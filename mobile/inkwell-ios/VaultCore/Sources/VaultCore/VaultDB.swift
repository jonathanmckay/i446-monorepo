import Foundation
import SQLite3

/// Thin, dependency-free wrapper over the system sqlite3. Enough for the
/// vault store: exec, prepared statements with positional binds, row reads,
/// transactions. Not thread-safe by itself; VaultStore serialises access on
/// its own queue.
public final class VaultDB {
    public struct Error: Swift.Error, CustomStringConvertible {
        public let message: String
        public var description: String { message }
    }

    private static let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)
    private var db: OpaquePointer?

    public init(path: String) throws {
        var handle: OpaquePointer?
        let flags = SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX
        guard sqlite3_open_v2(path, &handle, flags, nil) == SQLITE_OK, let h = handle else {
            let msg = handle.map { String(cString: sqlite3_errmsg($0)) } ?? "open failed"
            throw Error(message: "sqlite open \(path): \(msg)")
        }
        db = h
        try exec("PRAGMA journal_mode=WAL")
        try exec("PRAGMA synchronous=NORMAL")
        try exec("PRAGMA foreign_keys=ON")
    }

    deinit { if let d = db { sqlite3_close_v2(d) } }

    private func lastError() -> String {
        db.map { String(cString: sqlite3_errmsg($0)) } ?? "closed"
    }

    public func exec(_ sql: String) throws {
        guard sqlite3_exec(db, sql, nil, nil, nil) == SQLITE_OK else { throw Error(message: "\(lastError()) in: \(sql)") }
    }

    public func transaction<T>(_ body: () throws -> T) throws -> T {
        try exec("BEGIN IMMEDIATE")
        do {
            let out = try body()
            try exec("COMMIT")
            return out
        } catch {
            try? exec("ROLLBACK")
            throw error
        }
    }

    /// Runs a statement that returns no rows.
    public func run(_ sql: String, _ args: [Bindable?] = []) throws {
        let st = try prepare(sql, args)
        defer { sqlite3_finalize(st) }
        let rc = sqlite3_step(st)
        guard rc == SQLITE_DONE || rc == SQLITE_ROW else { throw Error(message: "\(lastError()) in: \(sql)") }
    }

    /// Runs a query and maps every row.
    public func query<T>(_ sql: String, _ args: [Bindable?] = [], _ map: (Row) throws -> T) throws -> [T] {
        let st = try prepare(sql, args)
        defer { sqlite3_finalize(st) }
        var out: [T] = []
        while true {
            let rc = sqlite3_step(st)
            if rc == SQLITE_ROW { out.append(try map(Row(st))) } else if rc == SQLITE_DONE { break } else { throw Error(message: "\(lastError()) in: \(sql)") }
        }
        return out
    }

    public func scalarInt(_ sql: String, _ args: [Bindable?] = []) throws -> Int {
        try query(sql, args) { $0.int(0) }.first ?? 0
    }

    public func scalarString(_ sql: String, _ args: [Bindable?] = []) throws -> String? {
        try query(sql, args) { $0.string(0) }.first ?? nil
    }

    public var changes: Int { Int(sqlite3_changes(db)) }

    private func prepare(_ sql: String, _ args: [Bindable?]) throws -> OpaquePointer {
        var st: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &st, nil) == SQLITE_OK, let s = st else {
            throw Error(message: "\(lastError()) in: \(sql)")
        }
        for (i, a) in args.enumerated() {
            let idx = Int32(i + 1)
            switch a {
            case nil: sqlite3_bind_null(s, idx)
            case let v as Int: sqlite3_bind_int64(s, idx, Int64(v))
            case let v as Int64: sqlite3_bind_int64(s, idx, v)
            case let v as Double: sqlite3_bind_double(s, idx, v)
            case let v as String: sqlite3_bind_text(s, idx, v, -1, VaultDB.transient)
            case let v as Data:
                _ = v.withUnsafeBytes { sqlite3_bind_blob(s, idx, $0.baseAddress, Int32(v.count), VaultDB.transient) }
            default: throw Error(message: "unsupported bind type at \(idx)")
            }
        }
        return s
    }

    public struct Row {
        let st: OpaquePointer
        init(_ st: OpaquePointer) { self.st = st }
        public func int(_ i: Int) -> Int { Int(sqlite3_column_int64(st, Int32(i))) }
        public func double(_ i: Int) -> Double { sqlite3_column_double(st, Int32(i)) }
        public func string(_ i: Int) -> String? {
            guard let c = sqlite3_column_text(st, Int32(i)) else { return nil }
            return String(cString: c)
        }
    }
}

public protocol Bindable {}
extension Int: Bindable {}
extension Int64: Bindable {}
extension Double: Bindable {}
extension String: Bindable {}
extension Data: Bindable {}
