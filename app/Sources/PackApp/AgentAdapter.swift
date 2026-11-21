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
    func streamEvents(for run: AgentRun) -> AsyncThrowingStream<AgentEvent, Error>
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

/// In-memory profile catalog. A durable SQLite repository will replace this
/// implementation after the schema/migration contract is reviewed.
actor AgentProfileCatalog {
    private var profiles: [UUID: AgentProfile] = [:]

    func add(_ profile: AgentProfile) throws {
        try profile.validate()
        guard profiles[profile.id] == nil else {
            throw AgentAdapterError.invalidConfiguration("Agent profile already exists")
        }
        profiles[profile.id] = profile
    }

    func profile(id: UUID) -> AgentProfile? {
        profiles[id]
    }

    func allProfiles() -> [AgentProfile] {
        profiles.values.sorted { $0.createdAt < $1.createdAt }
    }

    func updateConnection(id: UUID, state: AgentConnectionState, capabilities: Set<AgentCapability>) throws {
        guard var profile = profiles[id] else { throw AgentAdapterError.invalidConfiguration("Agent profile not found") }
        profile.connection = state
        profile.capabilities = capabilities
        profile.updatedAt = Date()
        profiles[id] = profile
    }

    func remove(id: UUID) -> AgentProfile? {
        profiles.removeValue(forKey: id)
    }
}

/// In-memory registry only. Persistence and UI ownership stay in later layers.
actor AgentAdapterRegistry {
    private var adapters: [String: any AgentAdapter] = [:]

    func register(_ adapter: any AgentAdapter) throws {
        guard adapters[adapter.providerID] == nil else {
            throw AgentAdapterError.invalidConfiguration("adapter already registered: \(adapter.providerID)")
        }
        adapters[adapter.providerID] = adapter
    }

    func adapter(for providerID: String) -> (any AgentAdapter)? {
        adapters[providerID]
    }

    func registeredProviderIDs() -> [String] {
        adapters.keys.sorted()
    }

    func remove(providerID: String) async {
        guard let adapter = adapters.removeValue(forKey: providerID) else { return }
        await adapter.disconnect()
    }
}
