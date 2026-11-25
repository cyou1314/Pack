import Foundation
import Security

public enum AgentVaultError: Error, Equatable {
    case invalidReference
    case unsupportedProvider
    case invalidSecret
    case duplicate
    case missing
    case keychainFailure(Int32)
}

/// API-only credential interface. Native sessions are owned by the browser.
public protocol AgentSecretBackend: Sendable {
    func insert(account: String, secret: Data) async throws
    func read(account: String) async throws -> Data
    func replace(account: String, secret: Data) async throws
    func remove(account: String) async throws
}

/// Device-local Data Protection Keychain. Never fall back to a file or sync.
public struct AgentKeychainBackend: AgentSecretBackend {
    private let service: String
    public init() { service = "com.cyou.pack.agent-api.v1" }
    init(service: String) { self.service = service }
    private func query(_ account: String) -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecUseDataProtectionKeychain as String: true,
            kSecAttrSynchronizable as String: false
        ]
    }

    private func check(_ status: OSStatus) throws {
        switch status {
        case errSecSuccess: return
        case errSecDuplicateItem: throw AgentVaultError.duplicate
        case errSecItemNotFound: throw AgentVaultError.missing
        default: throw AgentVaultError.keychainFailure(status)
        }
    }

    public func insert(account: String, secret: Data) async throws {
        var attributes = query(account)
        attributes[kSecValueData as String] = secret
        attributes[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        try check(SecItemAdd(attributes as CFDictionary, nil))
    }

    public func read(account: String) async throws -> Data {
        var attributes = query(account)
        attributes[kSecMatchLimit as String] = kSecMatchLimitOne
        attributes[kSecReturnData as String] = true
        var result: CFTypeRef?
        try check(SecItemCopyMatching(attributes as CFDictionary, &result))
        guard let data = result as? Data else { throw AgentVaultError.invalidSecret }
        return data
    }

    public func replace(account: String, secret: Data) async throws {
        try check(SecItemUpdate(query(account) as CFDictionary,
                                [kSecValueData as String: secret] as CFDictionary))
    }

    public func remove(account: String) async throws {
        try check(SecItemDelete(query(account) as CFDictionary))
    }
}

/// Construct references from UUIDs rather than accepting raw secrets as keys.
/// Old arbitrary references are not silently migrated or looked up.
public extension AgentCredentialReference {
    static func forProfile(id: UUID, mode: AgentConnectionMode) -> Self {
        .init(kind: mode == .officialAPI ? .apiKey : .nativeSession,
              vaultKey: id.uuidString.lowercased())
    }
}

/// Only trusted in-process provider adapters may call read. No IPC secret export.
/// Credential deletion is an explicit operation, not a registry side effect.
public struct AgentCredentialVault: Sendable {
    private let backend: any AgentSecretBackend
    public init(backend: any AgentSecretBackend = AgentKeychainBackend()) {
        self.backend = backend
    }

    private func account(_ profile: AgentProfile) throws -> String {
        try profile.validate()
        guard profile.mode == .officialAPI,
              profile.credentialReference == .forProfile(id: profile.id, mode: .officialAPI) else {
            throw AgentVaultError.invalidReference
        }
        guard AgentProviderCatalog.candidates.contains(where: {
            $0.id == profile.providerID && $0.modes.contains(.officialAPI)
        }) else { throw AgentVaultError.unsupportedProvider }
        return profile.providerID + ":" + profile.id.uuidString.lowercased()
    }

    private func bytes(_ key: String) throws -> Data {
        guard !key.isEmpty, key.utf8.count <= 8192,
              key.unicodeScalars.allSatisfy({ $0.value >= 33 && $0.value <= 126 }) else {
            throw AgentVaultError.invalidSecret
        }
        return Data(key.utf8)
    }

    public func insert(_ key: String, for profile: AgentProfile) async throws {
        try await backend.insert(account: account(profile), secret: bytes(key))
    }

    public func read(for profile: AgentProfile) async throws -> String {
        let data = try await backend.read(account: account(profile))
        guard let key = String(data: data, encoding: .utf8) else { throw AgentVaultError.invalidSecret }
        _ = try bytes(key)
        return key
    }

    public func replace(_ key: String, for profile: AgentProfile) async throws {
        try await backend.replace(account: account(profile), secret: bytes(key))
    }

    public func remove(for profile: AgentProfile) async throws {
        try await backend.remove(account: account(profile))
    }
}
