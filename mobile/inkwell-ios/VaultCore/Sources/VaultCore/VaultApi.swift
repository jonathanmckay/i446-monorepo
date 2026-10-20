import Foundation

/// Endpoint + token for `tools/vault-mobile/server.py`. The Funnel URL is
/// the default so the phone works with its Tailscale VPN off; the tailnet
/// path (`https://ix.tail9c51d5.ts.net/vault`) is the alternative.
public enum VaultConfig {
    public static let defaultBase = "https://ix.tail9c51d5.ts.net:8443/vault"
    static let kBase = "vault_base", kToken = "vault_token", kDevice = "vault_device"

    public static var base: String {
        get {
            let v = UserDefaults.standard.string(forKey: kBase)?.trimmingCharacters(in: .whitespaces) ?? ""
            return (v.isEmpty ? defaultBase : v).trimmingTrailingSlash()
        }
        set { UserDefaults.standard.set(newValue.trimmingCharacters(in: .whitespaces), forKey: kBase) }
    }
    /// Per-device bearer token minted on ix: `python3 server.py token <device>`.
    public static var token: String {
        get { UserDefaults.standard.string(forKey: kToken) ?? "" }
        set { UserDefaults.standard.set(newValue.trimmingCharacters(in: .whitespacesAndNewlines), forKey: kToken) }
    }
    public static var hasToken: Bool { !token.isEmpty }
}

extension String {
    func trimmingTrailingSlash() -> String {
        var s = self
        while s.hasSuffix("/") && s.count > 1 { s.removeLast() }
        return s
    }
}

public enum ManifestResult {
    case notModified
    case ok(Manifest, etag: String?)
    case error(String)
}

public struct FetchedFile {
    public let body: String
    public let sha: String          // X-Sha256 as served
    public let manifestSha: String  // X-Manifest-Sha256; differs if an agent wrote mid-sync
}

public enum FileResult {
    case ok(FetchedFile)
    case gone
    case error(String)
}

public enum DropResult {
    case ok(path: String, existing: Bool)
    case rejected(String)   // 4xx: do not retry the same payload
    case error(String)      // transport / 5xx: retry later
}

public final class VaultApi {
    private let session: URLSession
    private let base: () -> String
    private let token: () -> String

    public init(base: @escaping () -> String = { VaultConfig.base }, token: @escaping () -> String = { VaultConfig.token },
                session: URLSession? = nil) {
        self.base = base
        self.token = token
        if let s = session { self.session = s } else {
            let cfg = URLSessionConfiguration.ephemeral
            cfg.timeoutIntervalForRequest = 30
            cfg.timeoutIntervalForResource = 120
            cfg.waitsForConnectivity = false
            cfg.requestCachePolicy = .reloadIgnoringLocalCacheData
            self.session = URLSession(configuration: cfg)
        }
    }

    private func request(_ path: String, query: [String: String] = [:], method: String = "GET", body: Data? = nil, headers: [String: String] = [:]) -> URLRequest? {
        var comps = URLComponents(string: base() + path)
        if !query.isEmpty { comps?.queryItems = query.map { URLQueryItem(name: $0.key, value: $0.value) } }
        guard let url = comps?.url else { return nil }
        var req = URLRequest(url: url)
        req.httpMethod = method
        req.setValue("Bearer \(token())", forHTTPHeaderField: "Authorization")
        for (k, v) in headers { req.setValue(v, forHTTPHeaderField: k) }
        if let b = body {
            req.httpBody = b
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        return req
    }

    public func manifest(etag: String?) async -> ManifestResult {
        var h: [String: String] = [:]
        if let e = etag { h["If-None-Match"] = e }
        guard let req = request("/manifest", headers: h) else { return .error("bad base url") }
        do {
            let (data, resp) = try await session.data(for: req)
            let http = resp as? HTTPURLResponse
            let code = http?.statusCode ?? 0
            if code == 304 { return .notModified }
            guard code == 200 else { return .error(VaultApi.describeStatus(code, data)) }
            let m = try JSONDecoder().decode(Manifest.self, from: data)
            return .ok(m, etag: http?.value(forHTTPHeaderField: "ETag"))
        } catch let e as DecodingError {
            return .error("manifest decode: \(e)")
        } catch {
            return .error(VaultApi.describe(error))
        }
    }

    public func file(_ path: String) async -> FileResult {
        guard let req = request("/file", query: ["p": path]) else { return .error("bad base url") }
        do {
            let (data, resp) = try await session.data(for: req)
            let http = resp as? HTTPURLResponse
            let code = http?.statusCode ?? 0
            if code == 410 || code == 404 { return .gone }
            guard code == 200 else { return .error(VaultApi.describeStatus(code, data)) }
            guard let body = String(data: data, encoding: .utf8) else { return .error("\(path): not utf-8") }
            return .ok(FetchedFile(body: body,
                                   sha: http?.value(forHTTPHeaderField: "X-Sha256") ?? "",
                                   manifestSha: http?.value(forHTTPHeaderField: "X-Manifest-Sha256") ?? ""))
        } catch {
            return .error(VaultApi.describe(error))
        }
    }

    public func drop(_ c: Capture) async -> DropResult {
        var payload: [String: Any] = ["id": c.id, "created_at": c.createdAtString, "title": c.title, "body": c.body]
        if let t = c.target, !t.isEmpty { payload["target"] = t }
        if let m = c.mode { payload["mode"] = m }
        if let b = c.baseSha { payload["base_sha"] = b }
        guard let data = try? JSONSerialization.data(withJSONObject: payload),
              let req = request("/drop", method: "POST", body: data) else { return .error("bad request") }
        do {
            let (rdata, resp) = try await session.data(for: req)
            let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
            let json = (try? JSONSerialization.jsonObject(with: rdata)) as? [String: Any] ?? [:]
            switch code {
            case 200, 201:
                return .ok(path: json["path"] as? String ?? "", existing: json["existing"] as? Bool ?? false)
            case 400, 413:
                return .rejected(json["error"] as? String ?? "HTTP \(code)")
            default:
                return .error(VaultApi.describeStatus(code, rdata))
            }
        } catch {
            return .error(VaultApi.describe(error))
        }
    }

    static func describeStatus(_ code: Int, _ data: Data) -> String {
        switch code {
        case 401: return "token rejected"
        case 429: return "rate limited"
        case 507: return "subset too large on ix (edit z_meta/phone-subset.md)"
        default:
            let j = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            return (j?["error"] as? String).map { "HTTP \(code): \($0)" } ?? "HTTP \(code)"
        }
    }

    static func describe(_ error: Error) -> String {
        let e = error as NSError
        switch e.code {
        case NSURLErrorTimedOut: return "timed out"
        case NSURLErrorCannotConnectToHost, NSURLErrorCannotFindHost: return "ix unreachable"
        case NSURLErrorNotConnectedToInternet: return "offline"
        default: return e.localizedDescription
        }
    }
}
