import Foundation
import PackCore

enum HubError: Error {
    case badResponse(String)
    case toolError(String)
}

/// Hand-rolled MCP JSON-RPC client for the Pack hub.
/// Port of the proven runtime.mjs logic: all calls serialized through this
/// actor, session re-initialized transparently when the hub forgets it,
/// and the App coordinates URL discovery and client replacement.
actor HubClient {
    private let transport = HubTransport()
    private var cfg: PackConfig
    private var sessionId: String?
    private var rpcId = 0

    init(cfg: PackConfig) { self.cfg = cfg }

    // Discovery is coordinated by AppState so both identities use the same
    // persisted address, throttle and manual-override rule.
    func discoverBeforeSending() async { }

    private func post(body: [String: Any], isNotification: Bool) async throws -> (Data, HTTPURLResponse) {
        try await transport.post(
            config: cfg,
            sessionID: sessionId,
            body: body,
            isNotification: isNotification
        )
    }

    private func rpc(method: String, params: [String: Any]) async throws -> [String: Any] {
        rpcId += 1
        let body: [String: Any] = ["jsonrpc": "2.0", "id": rpcId, "method": method, "params": params]
        let (data, http) = try await post(body: body, isNotification: false)
        if let sid = http.value(forHTTPHeaderField: "mcp-session-id") { sessionId = sid }
        // Streamable HTTP may answer with SSE framing; take the data: line if so.
        var payload = data
        if let text = String(data: data, encoding: .utf8), text.contains("data:") {
            for line in text.components(separatedBy: .newlines) where line.hasPrefix("data:") {
                payload = Data(line.dropFirst(5).trimmingCharacters(in: .whitespaces).utf8)
            }
        }
        guard let obj = try? JSONSerialization.jsonObject(with: payload) as? [String: Any] else {
            throw HubError.badResponse("unparseable response")
        }
        if let err = obj["error"] as? [String: Any] {
            throw HubError.badResponse((err["message"] as? String) ?? "rpc error")
        }
        return (obj["result"] as? [String: Any]) ?? [:]
    }

    private func connect() async throws {
        sessionId = nil
        _ = try await rpc(method: "initialize", params: [
            "protocolVersion": "2025-03-26",
            "capabilities": [String: Any](),
            "clientInfo": ["name": "pack-app", "version": "0.1"],
        ])
        let note: [String: Any] = ["jsonrpc": "2.0", "method": "notifications/initialized"]
        _ = try? await post(body: note, isNotification: true)
    }

    private func callOnce(name: String, args: [String: Any]) async throws -> [String: Any] {
        if sessionId == nil { try await connect() }
        let result = try await rpc(method: "tools/call", params: ["name": name, "arguments": args])
        if let isErr = result["isError"] as? Bool, isErr {
            let texts = (result["content"] as? [[String: Any]])?.compactMap { $0["text"] as? String } ?? []
            throw HubError.toolError(texts.joined(separator: " "))
        }
        if let sc = result["structuredContent"] as? [String: Any] { return sc }
        if let content = result["content"] as? [[String: Any]],
           let text = content.first?["text"] as? String,
           let obj = try? JSONSerialization.jsonObject(with: Data(text.utf8)) as? [String: Any] {
            return obj
        }
        return [:]
    }

    func call(name: String, args: [String: Any] = [:]) async throws -> [String: Any] {
        do {
            return try await callOnce(name: name, args: args)
        } catch {
            let s = String(describing: error)
            if s.contains("Session not found") || s.contains("Missing session ID") {
                sessionId = nil
                return try await callOnce(name: name, args: args)
            }
            let category: String
            if let hubError = error as? HubError {
                switch hubError {
                case .badResponse: category = "bad_response"
                case .toolError: category = "tool_error"
                }
            } else {
                category = "network_or_io"
            }
            // Server error text can contain user data; keep logs to stable categories.
            NSLog("Pack App hub call %@ failed (%@)", name, category)
            throw error
        }
    }

}
