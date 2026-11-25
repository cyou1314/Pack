import Foundation
import Security
import WebKit

enum MemberHandoffError: LocalizedError {
    case noCookies, keychain(OSStatus), helper(String)
    var errorDescription: String? {
        switch self {
        case .noCookies: return "未找到 Muse 会话，请先由成员本人登录。"
        case .keychain(let code): return "钥匙串交接失败（\(code)）。"
        case .helper(let message): return message
        }
    }
}

enum MemberHandoff {
    private static let service = "com.cyou.pack.l2-handoff"

    @MainActor
    static func perform(member: String) async throws -> (String, String) {
        let store = WKWebsiteDataStore(forIdentifier: MemberLoginWebView.storeIdentifier(for: member))
        let all: [HTTPCookie] = await withCheckedContinuation { continuation in
            store.httpCookieStore.getAllCookies { continuation.resume(returning: $0) }
        }
        let cookies = all.filter {
            let domain = $0.domain.lowercased().trimmingCharacters(in: CharacterSet(charactersIn: "."))
            return domain == "muse.ai" || domain.hasSuffix(".muse.ai")
        }
        guard !cookies.isEmpty else { throw MemberHandoffError.noCookies }
        let rows: [[String: Any]] = cookies.map { cookie in
            var row: [String: Any] = ["name": cookie.name, "value": cookie.value, "domain": cookie.domain, "path": cookie.path, "secure": cookie.isSecure, "httpOnly": cookie.isHTTPOnly]
            if let expiry = cookie.expiresDate { row["expirationDate"] = expiry.timeIntervalSince1970 }
            if let sameSite = cookie.properties?[HTTPCookiePropertyKey("SameSite")] as? String { row["sameSite"] = sameSite }
            return row
        }
        let bytes = try JSONSerialization.data(withJSONObject: rows)
        guard bytes.count <= 2 * 1024 * 1024 else {
            throw MemberHandoffError.helper("会话 Cookie 数据过大，已阻止交接。")
        }
        let ticket = UUID().uuidString
        let request = try JSONSerialization.data(withJSONObject: ["member": member, "ticket": ticket]) + Data("\n".utf8)
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                    kSecAttrService as String: service,
                                    kSecAttrAccount as String: ticket]
        var item = query
        item[kSecValueData as String] = bytes
        item[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        let status = SecItemAdd(item as CFDictionary, nil)
        guard status == errSecSuccess else { throw MemberHandoffError.keychain(status) }
        let socket = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".pack/l2-ipc/handoff.sock").path
        let result: Data
        do {
            result = try await Task.detached(priority: .userInitiated) {
                try communicate(socket: socket, request: request)
            }.value
        } catch {
            let deleted = SecItemDelete(query as CFDictionary)
            guard deleted == errSecSuccess || deleted == errSecItemNotFound else { throw MemberHandoffError.helper("交接失败且临时钥匙串项清理失败，请检查钥匙串。") }
            throw error
        }
        let deleted = SecItemDelete(query as CFDictionary)
        guard deleted == errSecSuccess || deleted == errSecItemNotFound else { throw MemberHandoffError.helper("临时钥匙串项清理失败，请检查钥匙串。") }
        guard let body = try JSONSerialization.jsonObject(with: result) as? [String: Any] else { throw MemberHandoffError.helper("引擎交接响应无法识别。") }
        guard body["ok"] as? Bool == true else { throw MemberHandoffError.helper(body["error"] as? String ?? "会话交接失败。") }
        let state = body["state"] as? String ?? "unknown"
        return (state == "ready" ? "交接成功 · 会话检查通过" : "已加密交接 · 会话检查未就绪（\(state)）", state)
    }

    /// Remove abandoned one-time cookie payloads left by a crashed app/engine.
    static func cleanupStaleTickets(maxAge: TimeInterval = 600) {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecReturnAttributes as String: true,
            kSecMatchLimit as String: kSecMatchLimitAll
        ]
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        guard status == errSecSuccess, let items = result as? [[String: Any]] else { return }
        let cutoff = Date().addingTimeInterval(-maxAge)
        for item in items {
            guard let account = item[kSecAttrAccount as String] as? String,
                  let created = item[kSecAttrCreationDate as String] as? Date,
                  created < cutoff else { continue }
            let stale: [String: Any] = [
                kSecClass as String: kSecClassGenericPassword,
                kSecAttrService as String: service,
                kSecAttrAccount as String: account
            ]
            SecItemDelete(stale as CFDictionary)
        }
    }

    static func operation(_ op: String, member: String) async throws -> String {
        let request = try JSONSerialization.data(withJSONObject: ["op": op, "member": member]) + Data("\n".utf8)
        let socket = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".pack/l2-ipc/handoff.sock").path
        let data = try await Task.detached(priority: .userInitiated) { try communicate(socket: socket, request: request) }.value
        guard let body = try JSONSerialization.jsonObject(with: data) as? [String: Any], body["ok"] as? Bool == true, let state = body["state"] as? String else {
            throw MemberHandoffError.helper("引擎操作失败，请检查引擎或账号是否正忙。")
        }
        return state
    }

    private static func communicate(socket: String, request: Data) throws -> Data {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/nc")
        process.arguments = ["-U", "-w", String(DesignTokens.memberHandoffTimeoutSeconds), socket]
        let input = Pipe(), output = Pipe()
        process.standardInput = input; process.standardOutput = output; process.standardError = FileHandle.nullDevice
        do { try process.run() } catch { throw MemberHandoffError.helper("无法启动本机引擎交接通道。") }
        let timeoutFlag = HandoffTimeoutFlag()
        let timeoutWork = DispatchWorkItem {
            if process.isRunning {
                timeoutFlag.markTimedOut()
                process.terminate()
            }
        }
        DispatchQueue.global(qos: .utility).asyncAfter(
            deadline: .now() + .seconds(DesignTokens.memberHandoffTimeoutSeconds),
            execute: timeoutWork)
        defer { timeoutWork.cancel() }
        do { try input.fileHandleForWriting.write(contentsOf: request) }
        catch { process.terminate(); throw MemberHandoffError.helper("本机引擎交接通道不可用。") }
        try? input.fileHandleForWriting.close()
        var response = Data()
        do {
            while let chunk = try output.fileHandleForReading.read(upToCount: 4096), !chunk.isEmpty {
                guard response.count + chunk.count <= 64 * 1024 else {
                    process.terminate()
                    throw MemberHandoffError.helper("本机引擎响应超过安全上限。")
                }
                response.append(chunk)
            }
        } catch {
            if process.isRunning { process.terminate() }
            throw error
        }
        process.waitUntilExit()
        if timeoutFlag.timedOut {
            throw MemberHandoffError.helper("本机引擎交接超时；请检查引擎是否卡住。")
        }
        guard process.terminationStatus == 0, !response.isEmpty else { throw MemberHandoffError.helper("本机引擎未响应；请检查引擎和钥匙串授权。") }
        return response
    }
}

private final class HandoffTimeoutFlag {
    private let lock = NSLock()
    private var value = false

    func markTimedOut() {
        lock.lock()
        value = true
        lock.unlock()
    }

    var timedOut: Bool {
        lock.lock()
        defer { lock.unlock() }
        return value
    }
}
