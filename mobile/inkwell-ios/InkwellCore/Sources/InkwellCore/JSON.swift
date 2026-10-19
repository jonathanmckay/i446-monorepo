import Foundation

/// Thin dynamic view over a JSONSerialization object. Mirrors org.json's
/// optXxx semantics the Android client relies on: missing / null / wrong
/// type → the default, never a throw. Field names are the Flask payload's.
public struct JSON {
    public let raw: Any?
    public init(_ raw: Any?) { self.raw = raw }

    public static func parse(_ data: Data) -> JSON? {
        guard let o = try? JSONSerialization.jsonObject(with: data) else { return nil }
        return JSON(o)
    }
    public static func parse(_ text: String) -> JSON? { parse(Data(text.utf8)) }

    public var dict: [String: Any]? { raw as? [String: Any] }
    public var array: [Any]? { raw as? [Any] }
    public subscript(key: String) -> JSON { JSON(dict?[key]) }
    public func has(_ key: String) -> Bool { dict?[key] != nil }
    public func isNull(_ key: String) -> Bool { dict?[key] == nil || dict?[key] is NSNull }

    /// optString: "" when absent / null. Numbers are stringified (Toggl ids
    /// arrive as JSON numbers; `id` must round-trip as the same digits).
    public var string: String {
        switch raw {
        case let s as String: return s
        case let n as NSNumber where !(n is Bool): return n.stringValue
        default: return ""
        }
    }
    public func string(_ key: String, _ def: String = "") -> String {
        let v = self[key]; return v.raw == nil || v.raw is NSNull ? def : v.string
    }
    public func intOrNil(_ key: String) -> Int? {
        if isNull(key) { return nil }
        switch dict?[key] {
        case let n as NSNumber: return n.intValue
        case let s as String: return Int(s)
        default: return nil
        }
    }
    public func int(_ key: String, _ def: Int = 0) -> Int { intOrNil(key) ?? def }
    public func bool(_ key: String, _ def: Bool = false) -> Bool {
        switch dict?[key] {
        case let b as Bool: return b
        case let n as NSNumber: return n.boolValue
        case let s as String: return (s as NSString).boolValue
        default: return def
        }
    }
    public func strings(_ key: String) -> [String] {
        (self[key].array ?? []).compactMap { JSON($0).string.isEmpty ? nil : JSON($0).string }
    }
    public func objects(_ key: String) -> [JSON] { (self[key].array ?? []).map(JSON.init) }
}
