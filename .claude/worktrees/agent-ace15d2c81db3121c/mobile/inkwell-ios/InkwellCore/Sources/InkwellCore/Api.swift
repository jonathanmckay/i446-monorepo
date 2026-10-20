import Foundation

/// One JSON round-trip to Ix. Never throws: transport failures come back as
/// .error so every caller renders them (inline in the header, not only a
/// toast — a wedged tailnet otherwise looks like an empty day).
public enum ApiResult {
    case ok(JSON)
    case error(String)
}

public final class Api {
    private let session: URLSession

    public init() {
        let cfg = URLSessionConfiguration.ephemeral
        // /api/done and /api/log run did-fast.py (Todoist + Neon over ssh) —
        // several seconds on a good day. Reads must outlast that.
        cfg.timeoutIntervalForRequest = 45
        cfg.timeoutIntervalForResource = 60
        cfg.waitsForConnectivity = false
        cfg.requestCachePolicy = .reloadIgnoringLocalCacheData
        session = URLSession(configuration: cfg)
    }

    public func get(_ url: String) async -> ApiResult {
        guard let u = URL(string: url) else { return .error("bad url: \(url)") }
        return await run(URLRequest(url: u))
    }

    public func post(_ url: String, _ body: [String: Any]) async -> ApiResult {
        guard let u = URL(string: url) else { return .error("bad url: \(url)") }
        var req = URLRequest(url: u)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        guard let data = try? JSONSerialization.data(withJSONObject: body) else { return .error("bad body") }
        req.httpBody = data
        return await run(req)
    }

    private func run(_ req: URLRequest) async -> ApiResult {
        do {
            let (data, resp) = try await session.data(for: req)
            let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
            return Api.classify(data: data, status: code)
        } catch {
            return .error(Api.describe(error))
        }
    }

    /// Flask handlers answer {ok:false, error:...} with 4xx/5xx AND sometimes
    /// with 200; trust the body's own verdict first.
    public static func classify(data: Data, status: Int) -> ApiResult {
        guard let json = JSON.parse(data), json.dict != nil else { return .error("HTTP \(status): not JSON") }
        let httpOk = (200..<300).contains(status)
        if !json.bool("ok", httpOk) {
            let e = json.string("error")
            return .error(e.isEmpty ? "HTTP \(status)" : e)
        }
        return .ok(json)
    }

    static func describe(_ error: Error) -> String {
        let e = error as NSError
        switch e.code {
        case NSURLErrorTimedOut: return "timed out"
        case NSURLErrorCannotConnectToHost, NSURLErrorCannotFindHost: return "unreachable (tailnet down?)"
        case NSURLErrorNotConnectedToInternet: return "offline"
        default: return e.localizedDescription
        }
    }
}
