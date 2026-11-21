import Foundation

/// The connection mode is chosen explicitly when an Agent profile is created.
/// Native sessions and API credentials must never be silently interchanged.
enum AgentConnectionMode: String, Codable, CaseIterable, Sendable {
    case native
    case api
}

/// A credential reference only. Secret material belongs in macOS Keychain.
struct AgentCredentialReference: Codable, Hashable, Sendable {
    let keychainService: String
    let account: String
}

/// Provider-neutral configuration. It intentionally contains no password,
/// access token, cookie, or other secret value.
struct AgentAdapterConfiguration: Codable, Hashable, Sendable {
    let agentID: UUID
    let providerID: String
    let mode: AgentConnectionMode
    let credential: AgentCredentialReference?
    let endpoint: String?
    let model: String?
    let adapterVersion: String
}

/// Capabilities are probed per connection; unsupported features remain explicit.
enum AgentCapability: String, Codable, CaseIterable, Hashable, Sendable {
    case textInput
    case streamingOutput
    case sessionListing
    case sessionResume
    case attachments
    case cancellation
    case runStatus
    case usage
    case toolCalling
    case nativeMemory
}

/// Connection state must not imply that a capability is available.
enum AgentConnectionStatus: String, Codable, Sendable {
    case disconnected
    case connecting
    case connected
    case degraded
    case authenticationRequired
    case unsupported
}

struct AgentConnectionState: Codable, Hashable, Sendable {
    let status: AgentConnectionStatus
    let checkedAt: Date
    let message: String?
}

struct AgentSession: Codable, Hashable, Identifiable, Sendable {
    let id: String
    let title: String?
    let updatedAt: Date?
}

struct AgentAttachment: Codable, Hashable, Sendable {
    let id: String
    let filename: String
    let mimeType: String
    let byteCount: Int64
    let contentHash: String?
}

struct AgentMessageInput: Codable, Hashable, Sendable {
    let text: String
    let attachments: [AgentAttachment]
    let contextReferenceIDs: [String]
}

struct AgentRun: Codable, Hashable, Identifiable, Sendable {
    let id: String
    let agentID: UUID
    let sessionID: String?
    let createdAt: Date
}

enum AgentRunStatus: String, Codable, Sendable {
    case queued
    case running
    case completed
    case failed
    case cancelled
    case needsUserAction
    case unknown
}

/// A transport-neutral event envelope. Optional fields are interpreted by kind.
struct AgentEvent: Codable, Hashable, Sendable {
    enum Kind: String, Codable, Sendable {
        case messageDelta
        case messageCompleted
        case statusChanged
        case usageUpdated
        case approvalRequired
        case warning
    }

    let kind: Kind
    let text: String?
    let status: AgentRunStatus?
    let detail: String?
    let usageInputTokens: Int?
    let usageOutputTokens: Int?
}

struct AgentUsageSnapshot: Codable, Hashable, Sendable {
    let inputTokens: Int?
    let outputTokens: Int?
    let costAmount: Decimal?
    let currencyCode: String?
    let measuredAt: Date
}

enum AgentCancelResult: String, Codable, Sendable {
    case cancelled
    case alreadyFinished
    case unsupported
    case unconfirmed
}

enum AgentAdapterError: Error, Sendable {
    case unsupportedCapability(AgentCapability)
    case invalidConfiguration(String)
    case authenticationRequired
    case connectionFailed(String)
    case providerRejected(String)
    case runNotFound
    case cancellationUnconfirmed
}

/// Implementations must keep provider-private memory and credentials inside
/// the provider boundary. The Pack core only receives explicit references.
protocol AgentAdapter: Sendable {
    var providerID: String { get }
    var mode: AgentConnectionMode { get }

    func connect(configuration: AgentAdapterConfiguration) async throws -> AgentConnectionState
    func probeCapabilities() async throws -> Set<AgentCapability>
    func listSessions() async throws -> [AgentSession]
    func sendMessage(sessionID: String?, input: AgentMessageInput) async throws -> AgentRun
    func streamEvents(for run: AgentRun) async -> AsyncThrowingStream<AgentEvent, Error>
    func cancel(run: AgentRun) async throws -> AgentCancelResult
    func runStatus(for run: AgentRun) async throws -> AgentRunStatus
    func usageSnapshot() async throws -> AgentUsageSnapshot?
    func disconnect() async
}


enum AgentMemoryBoundary: String, Codable, Sendable {
    case providerPrivate
    case packManaged
}

/// A Pack-side record for one configured Agent account/profile.
/// It stores references and capability metadata, never provider secrets.
struct AgentProfile: Codable, Hashable, Identifiable, Sendable {
    let id: UUID
    var displayName: String
    let providerID: String
    let mode: AgentConnectionMode
    let credential: AgentCredentialReference?
    let endpoint: String?
    let model: String?
    let adapterVersion: String
    var capabilities: Set<AgentCapability>
    var connection: AgentConnectionState?
    let createdAt: Date
    var updatedAt: Date

    var memoryBoundary: AgentMemoryBoundary {
        mode == .native ? .providerPrivate : .packManaged
    }

    func validate() throws {
        guard !displayName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw AgentAdapterError.invalidConfiguration("display name is required")
        }
        guard !providerID.isEmpty,
              providerID.range(of: "^[a-z0-9][a-z0-9._-]{0,63}$", options: .regularExpression) != nil else {
            throw AgentAdapterError.invalidConfiguration("provider ID must be a stable lowercase identifier")
        }
        guard !adapterVersion.isEmpty else {
            throw AgentAdapterError.invalidConfiguration("adapter version is required")
        }
        if mode == .api {
            guard let credential,
                  !credential.keychainService.isEmpty,
                  !credential.account.isEmpty else {
                throw AgentAdapterError.invalidConfiguration("API Agent requires a valid Keychain credential reference")
            }
            guard let model, !model.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                throw AgentAdapterError.invalidConfiguration("API Agent requires a model identifier")
            }
        }
        if let endpoint {
            guard let url = URL(string: endpoint),
                  let host = url.host, !host.isEmpty,
                  let scheme = url.scheme?.lowercased(),
                  scheme == "https" || (scheme == "http" && (host == "localhost" || host == "127.0.0.1" || host == "::1")),
                  url.user == nil, url.password == nil else {
                throw AgentAdapterError.invalidConfiguration("endpoint must use HTTPS or a loopback HTTP address and contain no credentials")
            }
        }
    }
}

/// Keychain-backed secret storage. Only references are serialized in AgentProfile.
enum AgentCredentialVault {
    static func store(
        secret: String,
        service: String,
        account: String
    ) throws -> AgentCredentialReference {
        guard !secret.isEmpty, !service.isEmpty, !account.isEmpty else {
            throw AgentAdapterError.invalidConfiguration("credential, service and account are required")
        }
        let key: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account
        ]
        let data = Data(secret.utf8)
        let update: [String: Any] = [
            kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        ]
        let status = SecItemUpdate(key as CFDictionary, update as CFDictionary)
        if status == errSecItemNotFound {
            var add = key
            add.merge(update) { _, new in new }
            let added = SecItemAdd(add as CFDictionary, nil)
            guard added == errSecSuccess else {
                throw AgentAdapterError.invalidConfiguration("Keychain write failed: \(added)")
            }
        } else if status != errSecSuccess {
            throw AgentAdapterError.invalidConfiguration("Keychain update failed: \(status)")
        }

        let reference = AgentCredentialReference(keychainService: service, account: account)
        let verified = try read(reference)
        guard verified == secret else {
            throw AgentAdapterError.invalidConfiguration("Keychain read-back verification failed")
        }
        return reference
    }

    static func read(_ reference: AgentCredentialReference) throws -> String {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: reference.keychainService,
            kSecAttrAccount as String: reference.account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne
        ]
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        guard status == errSecSuccess,
              let data = result as? Data,
              let value = String(data: data, encoding: .utf8),
              !value.isEmpty else {
            throw AgentAdapterError.authenticationRequired
        }
        return value
    }

    static func delete(_ reference: AgentCredentialReference) throws {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: reference.keychainService,
            kSecAttrAccount as String: reference.account
        ]
        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw AgentAdapterError.invalidConfiguration("Keychain delete failed: \(status)")
        }
    }
}

/// One adapter instance belongs to one Agent profile, not one Provider.
/// This permits multiple accounts from the same provider to coexist.
actor AgentAdapterRegistry {
    private var adapters: [UUID: any AgentAdapter] = [:]

    func register(_ adapter: any AgentAdapter, for agentID: UUID) throws {
        guard adapters[agentID] == nil else {
            throw AgentAdapterError.invalidConfiguration("adapter already registered for Agent profile")
        }
        adapters[agentID] = adapter
    }

    func adapter(for agentID: UUID) -> (any AgentAdapter)? {
        adapters[agentID]
    }

    func registeredAgentIDs() -> [UUID] {
        adapters.keys.sorted { $0.uuidString < $1.uuidString }
    }

    func remove(agentID: UUID) async {
        guard let adapter = adapters.removeValue(forKey: agentID) else { return }
        await adapter.disconnect()
    }
}
