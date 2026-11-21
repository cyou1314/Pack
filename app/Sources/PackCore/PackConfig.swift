import Foundation
import Security

public enum CredentialReadResult: Equatable {
    case found(String)
    case notFound
    case invalidData
    case failure(Int32)
}

/// Minimal Keychain contract so migration and failure paths can be tested
/// without reading or writing the developer's real Keychain.
public protocol CredentialStore {
    func read(service: String, account: String) -> CredentialReadResult
    @discardableResult
    func add(service: String, account: String, value: String) -> Int32
}

public struct SystemKeychainCredentialStore: CredentialStore {
    public init() {}

    public func read(service: String, account: String) -> CredentialReadResult {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne
        ]
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound { return .notFound }
        guard status == errSecSuccess else { return .failure(status) }
        guard let data = result as? Data,
              let value = String(data: data, encoding: .utf8),
              !value.isEmpty else {
            return .invalidData
        }
        return .found(value)
    }

    @discardableResult
    public func add(service: String, account: String, value: String) -> Int32 {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
            kSecValueData as String: Data(value.utf8)
        ]
        return SecItemAdd(query as CFDictionary, nil)
    }
}

public struct PackConfig {
    public var hubUrl: String
    public var token: String
    public var ownerToken: String?

    public init(hubUrl: String, token: String, ownerToken: String? = nil) {
        self.hubUrl = hubUrl
        self.token = token
        self.ownerToken = ownerToken
    }

    public struct LoadResult {
        public let config: PackConfig?
        public let credentialStatus: String

        public init(config: PackConfig?, credentialStatus: String) {
            self.config = config
            self.credentialStatus = credentialStatus
        }
    }

    public static var configurationURL: URL {
        if let override = ProcessInfo.processInfo.environment["PACK_CONFIG_PATH"],
           override.hasPrefix("/") {
            return URL(fileURLWithPath: override)
        }
        return FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".pack/config.json")
    }

    public static func load() -> LoadResult {
        load(
            path: configurationURL,
            service: Bundle.main.bundleIdentifier ?? "com.cyou.pack",
            credentialStore: SystemKeychainCredentialStore()
        )
    }

    public static func load(
        path: URL,
        service: String,
        credentialStore: CredentialStore,
        fileManager: FileManager = .default
    ) -> LoadResult {
        guard secureConfigFile(path, fileManager: fileManager),
              let data = try? Data(contentsOf: path),
              var object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let configuredURL = object["hubUrl"] as? String,
              let endpoint = HubEndpoint.validatedURL(configuredURL) else {
            return LoadResult(config: nil, credentialStatus: "配置文件、文件权限或 Hub 地址无效。")
        }

        var values: [String: String] = [:]
        var migratedFields: [String] = []

        for (field, account) in [("token", "dispatcher"), ("ownerToken", "owner")] {
            let fileValue = (object[field] as? String).flatMap { $0.isEmpty ? nil : $0 }

            switch credentialStore.read(service: service, account: account) {
            case .found(let stored):
                guard !stored.isEmpty else {
                    return LoadResult(config: nil, credentialStatus: "钥匙串凭证为空，已阻止回退到配置文件。")
                }
                if let fileValue, fileValue != stored {
                    return LoadResult(config: nil, credentialStatus: "钥匙串与配置文件中的凭证不一致，已阻止连接。")
                }
                values[account] = stored
                if fileValue != nil { migratedFields.append(field) }

            case .notFound:
                guard let fileValue else { continue }
                _ = credentialStore.add(service: service, account: account, value: fileValue)

                switch credentialStore.read(service: service, account: account) {
                case .found(let verified) where verified == fileValue:
                    values[account] = verified
                    migratedFields.append(field)
                case .found:
                    return LoadResult(config: nil, credentialStatus: "钥匙串凭证冲突，已阻止使用配置文件凭证。")
                case .notFound:
                    return LoadResult(config: nil, credentialStatus: "钥匙串写入未生效，已阻止使用配置文件凭证。")
                case .invalidData:
                    return LoadResult(config: nil, credentialStatus: "钥匙串回读数据无效，已阻止使用配置文件凭证。")
                case .failure(let status):
                    return LoadResult(config: nil, credentialStatus: "钥匙串回读失败（状态 \(status)），已阻止使用配置文件凭证。")
                }

            case .invalidData:
                return LoadResult(config: nil, credentialStatus: "钥匙串凭证数据无效，已阻止回退到配置文件。")

            case .failure(let status):
                return LoadResult(config: nil, credentialStatus: "钥匙串读取失败（状态 \(status)），已阻止回退到配置文件。")
            }
        }

        guard let dispatcherToken = values["dispatcher"] else {
            return LoadResult(config: nil, credentialStatus: "未找到调度员凭证。")
        }

        if !migratedFields.isEmpty {
            do {
                // Do not overwrite a config file changed by another process while
                // Keychain migration was in progress.
                guard try Data(contentsOf: path) == data else {
                    throw CocoaError(.fileWriteUnknown)
                }
                for field in migratedFields {
                    object.removeValue(forKey: field)
                }
                let updated = try JSONSerialization.data(
                    withJSONObject: object,
                    options: [.prettyPrinted, .sortedKeys]
                )
                let temporary = path.deletingLastPathComponent()
                    .appendingPathComponent(".config-\(UUID().uuidString).tmp")
                guard fileManager.createFile(
                    atPath: temporary.path,
                    contents: updated,
                    attributes: [.posixPermissions: 0o600]
                ) else {
                    throw CocoaError(.fileWriteUnknown)
                }
                defer { try? fileManager.removeItem(at: temporary) }
                _ = try fileManager.replaceItemAt(path, withItemAt: temporary)
                try fileManager.setAttributes([.posixPermissions: 0o600], ofItemAtPath: path.path)
            } catch {
                return LoadResult(config: nil, credentialStatus: "钥匙串凭证已验证，但配置文件清理失败；已阻止连接。")
            }
        }

        return LoadResult(
            config: PackConfig(
                hubUrl: endpoint.absoluteString,
                token: dispatcherToken,
                ownerToken: values["owner"]
            ),
            credentialStatus: "凭证已从钥匙串读取。"
        )
    }

    private static func secureConfigFile(_ path: URL, fileManager: FileManager) -> Bool {
        do {
            // Do not follow a config-file symlink: otherwise an attacker or an
            // accidental link can redirect credential migration outside ~/.pack.
            let resourceValues = try path.resourceValues(forKeys: [.isSymbolicLinkKey])
            guard resourceValues.isSymbolicLink != true else { return false }

            let directory = path.deletingLastPathComponent()
            let directoryAttributes = try fileManager.attributesOfItem(atPath: directory.path)
            guard let directoryType = directoryAttributes[.type] as? FileAttributeType,
                  directoryType == .typeDirectory else {
                return false
            }
            let directoryPermissions = (directoryAttributes[.posixPermissions] as? NSNumber)?.intValue ?? 0o777
            if directoryPermissions != 0o700 {
                try fileManager.setAttributes([.posixPermissions: 0o700], ofItemAtPath: directory.path)
            }
            let verifiedDirectory = try fileManager.attributesOfItem(atPath: directory.path)
            let verifiedDirectoryPermissions = (verifiedDirectory[.posixPermissions] as? NSNumber)?.intValue ?? 0o777
            guard verifiedDirectoryPermissions == 0o700 else { return false }

            let attributes = try fileManager.attributesOfItem(atPath: path.path)
            guard let type = attributes[.type] as? FileAttributeType,
                  type == .typeRegular else {
                return false
            }
            let permissions = (attributes[.posixPermissions] as? NSNumber)?.intValue ?? 0o777
            if permissions != 0o600 {
                try fileManager.setAttributes([.posixPermissions: 0o600], ofItemAtPath: path.path)
            }
            let verified = try fileManager.attributesOfItem(atPath: path.path)
            let verifiedPermissions = (verified[.posixPermissions] as? NSNumber)?.intValue ?? 0o777
            return verifiedPermissions == 0o600
        } catch {
            return false
        }
    }
}
