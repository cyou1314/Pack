import Foundation

public enum AgentAccountResolutionError: Error, Equatable {
    case invalidReference
    case identityMismatch
    case chatUnavailable
}

/// Shared with the Hub/Node execution protocol. This is an opaque local
/// profile reference, not a credential or proof of provider login.
public struct AgentExecutionDestination: Codable, Equatable, Sendable {
    public let accountRef: String
    public let provider: String
    public let accessKind: String
    public init(accountRef: String, provider: String, accessKind: String) {
        self.accountRef = accountRef
        self.provider = provider
        self.accessKind = accessKind
    }
    private enum CodingKeys: String, CodingKey {
        case accountRef = "account_ref"
        case provider
        case accessKind = "access_kind"
    }
}

public enum AgentLocalAccountResolution: Equatable, Sendable {
    /// Credential exists in the local vault. Its upstream validity is unknown.
    case apiCredentialPresent(profileID: UUID)
    /// Native account must still be verified in its isolated browser session.
    case nativeSessionRequired(profileID: UUID)
}

/// Resolves only existing trusted registry profiles. Never creates a profile
/// or exports a secret/session. Provider requests remain in-process in the
/// trusted API adapter; native verification belongs to the browser host.
public struct AgentLocalAccountResolver: Sendable {
    private let registry: AgentRegistryStore
    private let vault: AgentCredentialVault
    public init(registry: AgentRegistryStore, vault: AgentCredentialVault = .init()) {
        self.registry = registry
        self.vault = vault
    }

    public func resolve(_ destination: AgentExecutionDestination) async throws -> AgentLocalAccountResolution {
        guard let id = UUID(uuidString: destination.accountRef),
              id.uuidString.lowercased() == destination.accountRef else {
            throw AgentAccountResolutionError.invalidReference
        }
        let profile = try await registry.profile(id: id)
        let expectedMode: AgentConnectionMode
        switch destination.accessKind {
        case "native": expectedMode = .nativeAccount
        case "api": expectedMode = .officialAPI
        default: throw AgentAccountResolutionError.identityMismatch
        }
        guard profile.providerID == destination.provider, profile.mode == expectedMode,
              profile.credentialReference == .forProfile(id: id, mode: expectedMode) else {
            throw AgentAccountResolutionError.identityMismatch
        }
        try profile.validate()
        guard profile.enabledCapabilities.contains(.chat) else {
            throw AgentAccountResolutionError.chatUnavailable
        }
        switch expectedMode {
        case .officialAPI:
            // Check device-local presence only, never return key bytes.
            _ = try await vault.read(for: profile)
            // Recheck after the async vault lookup to reject removed/changed
            // metadata. This is not a transactional provider request.
            guard try await registry.profile(id: id) == profile else {
                throw AgentAccountResolutionError.identityMismatch
            }
            return .apiCredentialPresent(profileID: id)
        case .nativeAccount:
            return .nativeSessionRequired(profileID: id)
        }
    }
}
