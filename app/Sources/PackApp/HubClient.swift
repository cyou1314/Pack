import Foundation
import Security
import PackCore

struct PackConfig {
    var hubUrl: String
    var token: String
    var ownerToken: String?

    struct LoadResult {
        let config: PackConfig?
        let credentialStatus: String
    }

    static var configurationURL: URL {
        if let override = ProcessInfo.processInfo.environment["PACK_CONFIG_PATH"], override.hasPrefix("/") {
            return URL(fileURLWithPath: override)
        }
        return FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".pack/config.json")
    }

    static func load() -> LoadResult {
        return load(path: configurationURL, service: Bundle.main.bundleIdentifier ?? "com.cyou.pack")
    }

    static func load(path: URL, service: String) -> LoadResult {
        guard let data = try? Data(contentsOf: path),
              var object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let url = object["hubUrl"] as? String else {
            return LoadResult(config: nil, credentialStatus: "无法读取本机配置。")
        }
        guard PackHubURLPolicy.isAllowed(url) else {
            return LoadResult(config: nil, credentialStatus: "Hub 地址不在允许范围内，已阻止发送凭证。")
        }
        var values: [String: String] = [:]
        var failure: String?
        var migratedFields: [String] = []
        for (field, account) in [("token", "dispatcher"), ("ownerToken", "owner")] {
            let fileValue = (object[field] as? String).flatMap { $0.isEmpty ? nil : $0 }
            let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                        kSecAttrService as String: service,
                                        kSecAttrAccount as String: account]
            var readQuery = query
            readQuery[kSecReturnData as String] = true
            readQuery[kSecMatchLimit as String] = kSecMatchLimitOne
            var result: CFTypeRef?
            let readStatus = SecItemCopyMatching(readQuery as CFDictionary, &result)
            if readStatus == errSecSuccess {
                guard let bytes = result as? Data, let stored = String(data: bytes, encoding: .utf8), !stored.isEmpty else {
                    failure = "钥匙串凭证无法读取，配置文件保持原样。"
                    if let fileValue { values[account] = fileValue }
                    continue
                }
                if let fileValue, fileValue != stored {
                    failure = "钥匙串与配置文件中的凭证不一致，配置文件保持原样。"
                    values[account] = fileValue
                } else {
                    values[account] = stored
                    if fileValue != nil { migratedFields.append(field) }
                }
            } else if readStatus == errSecItemNotFound, let fileValue {
                var addQuery = query
                addQuery[kSecValueData as String] = Data(fileValue.utf8)
                addQuery[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
                let added = SecItemAdd(addQuery as CFDictionary, nil)
                var verified: CFTypeRef?
                let check = added == errSecSuccess ? SecItemCopyMatching(readQuery as CFDictionary, &verified) : added
                if check == errSecSuccess, let bytes = verified as? Data, bytes == Data(fileValue.utf8) {
                    values[account] = fileValue
                    migratedFields.append(field)
                } else {
                    values[account] = fileValue
                    failure = "钥匙串写入或回读校验失败，配置文件保持原样。"
                }
            } else if readStatus != errSecItemNotFound {
                if let fileValue { values[account] = fileValue }
                failure = "钥匙串读取失败，配置文件保持原样。"
            }
        }
        if failure == nil && !migratedFields.isEmpty {
            do {
                guard try Data(contentsOf: path) == data else { throw CocoaError(.fileWriteUnknown) }
                for field in migratedFields { object.removeValue(forKey: field) }
                let updated = try JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys])
                let temporary = path.deletingLastPathComponent().appendingPathComponent(".config-\(UUID().uuidString).tmp")
                guard FileManager.default.createFile(atPath: temporary.path, contents: updated, attributes: [.posixPermissions: 0o600]) else { throw CocoaError(.fileWriteUnknown) }
                defer { try? FileManager.default.removeItem(at: temporary) }
                _ = try FileManager.default.replaceItemAt(path, withItemAt: temporary)
            } catch { failure = "凭证已写入钥匙串，但配置文件清理失败；原配置已保留。" }
        }
        guard let token = values["dispatcher"] else {
            return LoadResult(config: nil, credentialStatus: failure ?? "未找到调度员凭证。")
        }
        return LoadResult(config: PackConfig(hubUrl: url, token: token, ownerToken: values["owner"]),
                          credentialStatus: failure ?? "凭证已存入钥匙串。")
    }

}

enum HubError: Error {
    case badResponse(String)
    case toolError(String)
}

/// Hand-rolled MCP JSON-RPC client for the Pack hub.
/// Port of the proven runtime.mjs logic: all calls serialized through this
/// actor, session re-initialized transparently when the hub forgets it,
/// and the App coordinates URL discovery and client replacement.
actor HubClient {
    private var cfg: PackConfig
    private var sessionId: String?
    private var rpcId = 0
    private let session: URLSession = {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.httpCookieStorage = nil
        configuration.urlCache = nil
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        return URLSession(configuration: configuration, delegate: NoHubRedirects(), delegateQueue: nil)
    }()

    init(cfg: PackConfig) { self.cfg = cfg }

    // Discovery is coordinated by AppState so both identities use the same
    // persisted address, throttle and manual-override rule.
    func discoverBeforeSending() async { }

    private func post(body: [String: Any], isNotification: Bool) async throws -> (Data, HTTPURLResponse) {
        guard PackHubURLPolicy.isAllowed(cfg.hubUrl),
              let url = URL(string: cfg.hubUrl) else {
            throw HubError.badResponse("untrusted hub url")
        }
        var req = URLRequest(url: url)
        req.timeoutInterval = 20
        req.cachePolicy = .reloadIgnoringLocalCacheData
        req.setValue("no-store", forHTTPHeaderField: "Cache-Control")
        req.setValue("close", forHTTPHeaderField: "Connection")
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue("application/json, text/event-stream", forHTTPHeaderField: "Accept")
        req.setValue("Bearer \(cfg.token)", forHTTPHeaderField: "Authorization")
        if let sid = sessionId { req.setValue(sid, forHTTPHeaderField: "mcp-session-id") }
        req.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (data, resp) = try await session.data(for: req)
        guard let http = resp as? HTTPURLResponse else { throw HubError.badResponse("no http response") }
        if isNotification { return (data, http) }
        if http.statusCode != 200 { throw HubError.badResponse("http \(http.statusCode)") }
        return (data, http)
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
            NSLog("Pack App hub call %@ failed: %@", name, String(describing: error))
            throw error
        }
    }

}

/// Hub requests carry dispatcher credentials. Never follow redirects, even
/// when the original host passed the Pack URL allowlist.
private final class NoHubRedirects: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}
