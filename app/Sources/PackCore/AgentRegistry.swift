import Foundation

/// Candidate catalog only: a listed mode is not an implemented connector.
public struct AgentProvider: Equatable, Sendable {
    public let id: String
    public let displayName: String
    public let modes: Set<AgentConnectionMode>
    public init(id: String, displayName: String, modes: Set<AgentConnectionMode>) {
        self.id = id
        self.displayName = displayName
        self.modes = modes
    }
}

public enum AgentProviderCatalog {
    public static let candidates: [AgentProvider] = [
        .init(id: "muse", displayName: "Muse", modes: [.nativeAccount]),
        .init(id: "chatgpt", displayName: "ChatGPT", modes: [.nativeAccount]),
        .init(id: "openai", displayName: "OpenAI API", modes: [.officialAPI]),
        .init(id: "gemini", displayName: "Gemini", modes: [.nativeAccount]),
        .init(id: "gemini-api", displayName: "Gemini API", modes: [.officialAPI]),
        .init(id: "doubao", displayName: "豆包", modes: [.nativeAccount]),
        .init(id: "volcengine", displayName: "火山引擎 API", modes: [.officialAPI])
    ]
}

public enum AgentRegistryError: Error, Equatable {
    case unknownProvider
    case unsupportedMode
    case duplicateProfile
    case credentialAlreadyInUse
    case immutableIdentity
    case unknownProfile
    case unsupportedSchema
    case unsafeStorage
}

/// A validated collection. Profile identity and credential reference cannot
/// change in place; create a new profile when switching accounts or modes.
public struct AgentRegistry: Sendable {
    private var records: [UUID: AgentProfile] = [:]
    public let providers: [AgentProvider]
    public var profiles: [AgentProfile] {
        records.values.sorted { $0.id.uuidString < $1.id.uuidString }
    }

    public init(providers: [AgentProvider] = AgentProviderCatalog.candidates) {
        self.providers = providers
    }

    private func validate(_ profile: AgentProfile) throws {
        try profile.validate()
        guard let provider = providers.first(where: { $0.id == profile.providerID }) else {
            throw AgentRegistryError.unknownProvider
        }
        guard provider.modes.contains(profile.mode) else {
            throw AgentRegistryError.unsupportedMode
        }
        // Vault keys must be unique across the registry, not merely per provider.
        if records.values.contains(where: {
            $0.id != profile.id && $0.credentialReference?.vaultKey == profile.credentialReference?.vaultKey
        }) {
            throw AgentRegistryError.credentialAlreadyInUse
        }
    }

    public mutating func insert(_ profile: AgentProfile) throws {
        guard records[profile.id] == nil else { throw AgentRegistryError.duplicateProfile }
        try validate(profile)
        records[profile.id] = profile
    }

    public mutating func update(_ profile: AgentProfile) throws {
        guard let old = records[profile.id] else { throw AgentRegistryError.unknownProfile }
        guard old.providerID == profile.providerID, old.mode == profile.mode,
              old.credentialReference == profile.credentialReference else {
            throw AgentRegistryError.immutableIdentity
        }
        try validate(profile)
        records[profile.id] = profile
    }

    /// Removes metadata only. Vault deletion and native session cleanup are
    /// separate operations requiring an explicit user decision.
    public mutating func remove(id: UUID) throws {
        guard records.removeValue(forKey: id) != nil else { throw AgentRegistryError.unknownProfile }
    }

    /// Directory name only; callers must enforce private storage and one writer.
    public func nativeSessionDirectoryName(id: UUID) throws -> String {
        guard let profile = records[id] else { throw AgentRegistryError.unknownProfile }
        guard profile.mode == .nativeAccount else { throw AgentRegistryError.unsupportedMode }
        return id.uuidString.lowercased()
    }

    private struct Snapshot: Codable {
        let schemaVersion: Int
        let profiles: [AgentProfile]
    }

    public func encoded() throws -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        return try encoder.encode(Snapshot(schemaVersion: 1, profiles: profiles))
    }

    public static func decode(_ data: Data, providers: [AgentProvider] = AgentProviderCatalog.candidates) throws -> AgentRegistry {
        let snapshot = try JSONDecoder().decode(Snapshot.self, from: data)
        guard snapshot.schemaVersion == 1 else { throw AgentRegistryError.unsupportedSchema }
        var registry = AgentRegistry(providers: providers)
        for profile in snapshot.profiles { try registry.insert(profile) }
        return registry
    }
}

public enum AgentProvisioningError: Error, Equatable {
    case operationInProgress
    /// Metadata was not committed and credential cleanup failed.
    /// Retain the profile identity for explicit recovery; never retry blindly.
    case credentialCleanupRequired(profileID: UUID)
}

/// Single-process metadata store. No secret material is written deliberately;
/// callers must provide opaque vault references, not raw credential strings.
public actor AgentRegistryStore {
    private let file: URL
    private var registry: AgentRegistry
    private var provisioning = false

    private static func attributesIfPresent(_ url: URL) throws -> [FileAttributeKey: Any]? {
        do {
            return try FileManager.default.attributesOfItem(atPath: url.path)
        } catch let error as CocoaError
            where error.code == .fileNoSuchFile || error.code == .fileReadNoSuchFile {
            return nil
        }
    }

    /// Caller supplies a dedicated, already-created private directory.
    /// Never creates or modifies existing application data on initialization.
    public init(directory: URL, providers: [AgentProvider] = AgentProviderCatalog.candidates) throws {
        guard directory.isFileURL else { throw AgentRegistryError.unsafeStorage }
        let fm = FileManager.default
        let attrs = try fm.attributesOfItem(atPath: directory.path)
        guard attrs[.type] as? FileAttributeType == .typeDirectory,
              let mode = attrs[.posixPermissions] as? NSNumber,
              mode.intValue & 0o077 == 0 else { throw AgentRegistryError.unsafeStorage }
        let target = directory.appendingPathComponent("agent-registry.json", isDirectory: false)
        self.file = target
        if let attrs = try Self.attributesIfPresent(target) {
            guard attrs[.type] as? FileAttributeType == .typeRegular else {
                throw AgentRegistryError.unsafeStorage
            }
            self.registry = try AgentRegistry.decode(Data(contentsOf: target), providers: providers)
        } else {
            self.registry = AgentRegistry(providers: providers)
        }
    }

    public func list() -> [AgentProfile] { registry.profiles }

    private func persist(_ next: AgentRegistry) throws {
        if let attrs = try Self.attributesIfPresent(file),
           attrs[.type] as? FileAttributeType != .typeRegular {
            throw AgentRegistryError.unsafeStorage
        }
        try next.encoded().write(to: file, options: .atomic)
        registry = next
    }

    /// Creates a new API profile and its credential as one compensated operation.
    /// The store must have a single owner for its directory. Other writers and
    /// process termination between Keychain and disk are not covered.
    public func provisionAPIProfile(_ profile: AgentProfile, apiKey: String,
                                    vault: AgentCredentialVault) async throws {
        try ensureIdle()
        guard profile.mode == .officialAPI else { throw AgentRegistryError.unsupportedMode }
        var next = registry
        try next.insert(profile)
        provisioning = true
        defer { provisioning = false }

        try Task.checkCancellation()
        // Duplicate or failed inserts are not ours to remove.
        try await vault.insert(apiKey, for: profile)
        do {
            try Task.checkCancellation()
            try persist(next)
        } catch {
            let provisioningError = error
            do {
                try await vault.remove(for: profile)
            } catch AgentVaultError.missing {
                // The desired rollback state already holds.
            } catch {
                throw AgentProvisioningError.credentialCleanupRequired(profileID: profile.id)
            }
            throw provisioningError
        }
    }

    private func ensureIdle() throws {
        guard !provisioning else { throw AgentProvisioningError.operationInProgress }
    }

    /// Metadata-only insertion retained for legacy/import callers.
    /// New API profiles should use provisionAPIProfile.
    public func insert(_ profile: AgentProfile) throws {
        try ensureIdle()
        var next = registry
        try next.insert(profile)
        try persist(next)
    }

    public func update(_ profile: AgentProfile) throws {
        try ensureIdle()
        var next = registry
        try next.update(profile)
        try persist(next)
    }

    public func remove(id: UUID) throws {
        try ensureIdle()
        var next = registry
        try next.remove(id: id)
        try persist(next)
    }
}
