import Foundation
import SwiftUI
import Security
import PackCore

@MainActor
extension AppState {
    // MARK: - Connection, credentials and endpoint discovery

    func refreshAddrTokenStatus() {
        let status = AddressSource.status()
        addrTokenStatus = status == errSecSuccess ? "地址源令牌已存入钥匙串" : (status == errSecItemNotFound ? "地址源令牌未配置" : "地址源令牌状态无法读取")
    }

    func saveAddrToken() {
        let status = AddressSource.save(settingsAddrToken)
        settingsAddrToken = ""
        if status == errSecSuccess || status == errSecItemNotFound { refreshAddrTokenStatus() }
        else { addrTokenStatus = "地址源令牌保存失败（钥匙串状态 \(status)）" }
    }

    func saveHubURL() {
        if persistHubURL(settingsHubURL.trimmingCharacters(in: .whitespacesAndNewlines), manual: true) {
            Task { await refresh() }
        }
    }

    // Manual and automatic updates share the same atomic configuration write
    // and credential reload. Automatic failures leave settings silent.
    private func persistHubURL(_ text: String, manual: Bool) -> Bool {
        func fail(_ message: String) -> Bool {
            if manual { settingsMessage = message }
            return false
        }
        guard let endpoint = HubEndpoint.validatedURL(text) else {
            return fail("地址无效：远程 Hub 必须使用 HTTPS；HTTP 仅允许本机回环地址，且不能包含凭证、查询参数或片段。")
        }
        let normalizedURL = endpoint.absoluteString
        let path = PackConfig.configurationURL
        do {
            let loaded = PackConfig.load()
            credentialStatus = loaded.credentialStatus
            guard var cfg = loaded.config else { return fail("无法读取本机凭证") }
            let data = try Data(contentsOf: path)
            guard var object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { return fail("配置格式无效") }
            object["hubUrl"] = normalizedURL
            let updated = try JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys])
            let temporary = path.deletingLastPathComponent().appendingPathComponent(".config-\(UUID().uuidString).tmp")
            guard FileManager.default.createFile(atPath: temporary.path, contents: updated, attributes: [.posixPermissions: 0o600]) else { return fail("无法创建安全的配置临时文件") }
            defer { try? FileManager.default.removeItem(at: temporary) }
            _ = try FileManager.default.replaceItemAt(path, withItemAt: temporary)
            if manual { manualHubURLSaved = true }
            storedHubURL = normalizedURL
            cfg.hubUrl = normalizedURL
            installHubClients(cfg)
            settingsHubURL = normalizedURL
            if manual { settingsMessage = "地址已保存，正在连接" }
            return true
        } catch { return fail("保存失败，请检查本机配置后重试") }
    }

    private func installHubClients(_ cfg: PackConfig) {
        clientGeneration += 1
        questionResponderName = nil
        localQuestionAnswers.removeAll()
        client = HubClient(cfg: cfg)
        if let token = cfg.ownerToken, !token.isEmpty {
            var ownerCfg = cfg; ownerCfg.token = token; ownerClient = HubClient(cfg: ownerCfg)
        } else { ownerClient = nil }
    }

    private func rebuildHubClients() -> Bool {
        let loaded = PackConfig.load()
        credentialStatus = loaded.credentialStatus
        guard var cfg = loaded.config else { return false }
        cfg.hubUrl = storedHubURL
        installHubClients(cfg)
        return true
    }

    func followPublishedHubURL(rebuildOnFailure: Bool = false) async -> Bool {
        guard !discoveryInProgress, rebuildOnFailure || !manualHubURLSaved else { return false }
        if let host = URLComponents(string: storedHubURL)?.host?.lowercased(), ["127.0.0.1", "localhost", "::1", "[::1]"].contains(host) {
            return rebuildOnFailure && rebuildHubClients()
        }
        let now = ProcessInfo.processInfo.systemUptime
        if let last = lastHubDiscoveryUptime, now - last < hubDiscoveryInterval { return false }
        lastHubDiscoveryUptime = now
        discoveryInProgress = true
        defer { discoveryInProgress = false }
        if manualHubURLSaved { return rebuildOnFailure && rebuildHubClients() }
        let generation = clientGeneration
        if let value = await AddressSource.privateURL(timeout: hubDiscoveryTimeout) {
            guard generation == clientGeneration, !manualHubURLSaved else { return false }
            if value != storedHubURL, persistHubURL(value, manual: false) { return true }
            if value == storedHubURL { return rebuildOnFailure && rebuildHubClients() }
        }
        guard generation == clientGeneration, !manualHubURLSaved else { return false }
        guard let source = URL(string: "https://raw.githubusercontent.com/cyou1314/Pack/main/runtime/hub-url.txt") else {
            return rebuildOnFailure && rebuildHubClients()
        }
        var request = URLRequest(url: source, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: hubDiscoveryTimeout)
        request.setValue("no-cache", forHTTPHeaderField: "Cache-Control")
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            NSLog("地址源 raw.githubusercontent.com HTTP %d", (response as? HTTPURLResponse)?.statusCode ?? 0)
            // A manual save or another client change while the request was in flight wins.
            guard generation == clientGeneration, !manualHubURLSaved else { return false }
            if let http = response as? HTTPURLResponse, http.statusCode == 200,
               let value = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines),
               let parts = URLComponents(string: value), parts.scheme?.lowercased() == "https",
               let host = parts.host, !host.isEmpty, parts.user == nil, parts.password == nil,
               parts.query == nil, parts.fragment == nil, value != storedHubURL,
               persistHubURL(value, manual: false) { return true }
        } catch {
            guard generation == clientGeneration, !manualHubURLSaved else { return false }
        }
        return rebuildOnFailure && generation == clientGeneration && rebuildHubClients()
    }
}
