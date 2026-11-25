import Foundation

public enum AgentConnectionMode: String, Codable, CaseIterable, Sendable {
    /// Uses the user's existing provider account and its native login/session.
    case nativeAccount
    /// Uses a provider's official API with a Pack-managed credential reference.
    case officialAPI
}

public enum AgentContextBoundary: String, Codable, Sendable {
    /// Context, account history and provider-side tools remain with the provider account.
    case providerAccount
    /// Context is assembled and governed by the Pack workspace.
    case packWorkspace
}

public enum AgentMemoryBoundary: String, Codable, Sendable {
    /// Memory is owned and managed by the provider account; Pack does not import it.
    case providerManaged
    /// Memory is owned and managed by Pack for this API-connected profile.
    case packManaged
    /// The profile does not use persistent memory.
    case disabled
}

public enum AgentCredentialKind: String, Codable, Sendable {
    case nativeSession
    case apiKey
}

public enum AgentCapability: String, Codable, CaseIterable, Sendable {
    case chat
    case toolCalling
    case fileAccess
    case browserAutomation
    case computerUse
    case imageGeneration
    case videoGeneration
    case audio
    case codeExecution
}

/// A pointer to a secret or native account session. It must never contain the
/// credential itself; secret material belongs in Keychain or a provider-owned
/// session store.
public struct AgentCredentialReference: Codable, Equatable, Sendable {
    public let kind: AgentCredentialKind
    public let vaultKey: String

    public init(kind: AgentCredentialKind, vaultKey: String) {
        self.kind = kind
        self.vaultKey = vaultKey
    }
}

public enum AgentProfileValidationError: Error, Equatable {
    case emptyProviderID
    case emptyDisplayName
    case missingCredentialReference
    case emptyCredentialReference
    case credentialModeMismatch
    case contextBoundaryMismatch
    case memoryBoundaryMismatch
}

/// Provider-neutral configuration for one connected Agent identity.
///
/// Native-account and official-API profiles are deliberately distinct:
/// API profiles cannot claim provider-account context or provider-managed
/// personal memory. The model stores references only, never API keys or cookies.
public struct AgentProfile: Codable, Equatable, Identifiable, Sendable {
    public let id: UUID
    public var providerID: String
    public var displayName: String
    public var mode: AgentConnectionMode
    public var modelID: String?
    public var accountLabel: String?
    public var credentialReference: AgentCredentialReference?
    public var contextBoundary: AgentContextBoundary
    public var memoryBoundary: AgentMemoryBoundary
    public var enabledCapabilities: Set<AgentCapability>

    public init(
        id: UUID = UUID(),
        providerID: String,
        displayName: String,
        mode: AgentConnectionMode,
        modelID: String? = nil,
        accountLabel: String? = nil,
        credentialReference: AgentCredentialReference?,
        contextBoundary: AgentContextBoundary,
        memoryBoundary: AgentMemoryBoundary,
        enabledCapabilities: Set<AgentCapability> = [.chat]
    ) {
        self.id = id
        self.providerID = providerID
        self.displayName = displayName
        self.mode = mode
        self.modelID = modelID
        self.accountLabel = accountLabel
        self.credentialReference = credentialReference
        self.contextBoundary = contextBoundary
        self.memoryBoundary = memoryBoundary
        self.enabledCapabilities = enabledCapabilities
    }

    public func validate() throws {
        guard !providerID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw AgentProfileValidationError.emptyProviderID
        }
        guard !displayName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw AgentProfileValidationError.emptyDisplayName
        }
        guard let credentialReference else {
            throw AgentProfileValidationError.missingCredentialReference
        }
        guard !credentialReference.vaultKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw AgentProfileValidationError.emptyCredentialReference
        }

        switch mode {
        case .nativeAccount:
            guard credentialReference.kind == .nativeSession else {
                throw AgentProfileValidationError.credentialModeMismatch
            }
            guard contextBoundary == .providerAccount else {
                throw AgentProfileValidationError.contextBoundaryMismatch
            }
            guard memoryBoundary == .providerManaged || memoryBoundary == .disabled else {
                throw AgentProfileValidationError.memoryBoundaryMismatch
            }
        case .officialAPI:
            guard credentialReference.kind == .apiKey else {
                throw AgentProfileValidationError.credentialModeMismatch
            }
            guard contextBoundary == .packWorkspace else {
                throw AgentProfileValidationError.contextBoundaryMismatch
            }
            guard memoryBoundary == .packManaged || memoryBoundary == .disabled else {
                throw AgentProfileValidationError.memoryBoundaryMismatch
            }
        }
    }
}
