import Foundation

/// Code availability, not a live Provider capability declaration.
public enum AgentIntegrationStage: String, Codable, Sendable {
    case legacyCompatibility
    case nativeLoginDevelopment
    case nativeDriverCandidate
    case manualTextAPI
    case unsupported
}

/// Read-only report derived from the local profile and credential inventory.
/// Hub enrollment, account proof and a successful remote request are separate
/// evidence sources; none is asserted by this report.
public struct AgentConnectionReport: Equatable, Sendable {
    public let stage: AgentIntegrationStage
    public let configuredCapabilities: [String]
    public let implementedCapabilities: [String]
    public let verifiedCapabilities: [String]
    public let credentialState: String
    public let identityVerified: Bool
    public let executionVerified: Bool
    public let automaticExecutionEnabled: Bool

    public init(profile: AgentProfile, credentialState: AgentLocalCredentialState) {
        switch (profile.providerID,profile.mode) {
        case ("muse",.nativeAccount): stage = .legacyCompatibility
        case ("chatgpt",.nativeAccount): stage = .nativeDriverCandidate
        case ("gemini",.nativeAccount),("doubao",.nativeAccount): stage = .nativeLoginDevelopment
        case ("openai",.officialAPI),("gemini-api",.officialAPI),("volcengine",.officialAPI):
            stage = .manualTextAPI
        default: stage = .unsupported
        }
        self.credentialState = credentialState.rawValue
        configuredCapabilities = profile.enabledCapabilities.map(\.rawValue).sorted()
        // The modern, manually authorized API lane implements text only.
        // Legacy paths and candidate browser fixtures are not modern
        // verified execution capabilities.
        implementedCapabilities = stage == .manualTextAPI ? ["message.send"] : []
        verifiedCapabilities = []
        identityVerified = false
        executionVerified = false
        automaticExecutionEnabled = false
    }
}
