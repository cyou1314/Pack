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
    static func perform(member: String) async throws -> String {
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
        return state == "ready" ? "交接成功 · 会话检查通过" : "已加密交接 · 会话检查未就绪（\(state)）"
    }

    private static func communicate(socket: String, request: Data) throws -> Data {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/nc")
        process.arguments = ["-U", "-w", String(DesignTokens.memberHandoffTimeoutSeconds), socket]
        let input = Pipe(), output = Pipe()
        process.standardInput = input; process.standardOutput = output; process.standardError = FileHandle.nullDevice
        do { try process.run() } catch { throw MemberHandoffError.helper("无法启动本机引擎交接通道。") }
        do { try input.fileHandleForWriting.write(contentsOf: request) }
        catch { process.terminate(); throw MemberHandoffError.helper("本机引擎交接通道不可用。") }
        let response = output.fileHandleForReading.readDataToEndOfFile()
        try? input.fileHandleForWriting.close()
        process.waitUntilExit()
        guard process.terminationStatus == 0, !response.isEmpty else { throw MemberHandoffError.helper("本机引擎未响应；请检查引擎和钥匙串授权。") }
        return response
    }
}
