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
