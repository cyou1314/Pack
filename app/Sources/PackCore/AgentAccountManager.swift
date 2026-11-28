import Foundation
import Darwin

public enum AgentAccountSetupError: Error, Equatable {
    case invalidConfiguration
    case unsafeDirectory
    case registryInUse
}

public enum AgentLocalCredentialState: String, Sendable {
    case nativeSessionUnverified
    case apiKeyMissing
    case apiKeyPresentUnverified
    case apiKeyUnavailable
}

public struct AgentAccountSummary: Identifiable, Sendable {
    public var id: UUID { profile.id }
    public let profile: AgentProfile
    public let credentialState: AgentLocalCredentialState
    public var connectionReport: AgentConnectionReport {
        .init(profile:profile,credentialState:credentialState)
    }
}

/// Local metadata and insert-only credential setup. Deliberately not a Hub
/// member provisioner, a login verifier, or a provider network client.
public actor AgentAccountManager {
    private let directory: URL
    private let vault: AgentCredentialVault
    private var store: AgentRegistryStore?
    private var lockFD: Int32 = -1
    private var admissions: [String:AgentCollaborationAdmission] = [:]

    public init(directory: URL, vault: AgentCredentialVault = .init()) {
        self.directory = directory
        self.vault = vault
    }

    deinit {
        if lockFD >= 0 { _ = Darwin.close(lockFD) }
    }

    private func checkDirectory(_ path: String, privateOnly: Bool) throws {
        var info = stat()
        guard lstat(path, &info) == 0, (info.st_mode & S_IFMT) == S_IFDIR,
              info.st_uid == getuid(), (info.st_mode & (privateOnly ? 0o077 : 0o022)) == 0
        else { throw AgentAccountSetupError.unsafeDirectory }
    }

    private func registry() throws -> AgentRegistryStore {
        if let store { return store }
        guard directory.isFileURL, directory.path.hasPrefix("/") else {
            throw AgentAccountSetupError.unsafeDirectory
        }
        // Never follow a symlink at the app-owned directory or its parent.
        try checkDirectory(directory.deletingLastPathComponent().path, privateOnly:false)
        if mkdir(directory.path, 0o700) != 0 && errno != EEXIST {
            throw AgentAccountSetupError.unsafeDirectory
        }
        try checkDirectory(directory.path, privateOnly:true)
        let target = directory.appendingPathComponent("agent-registry.json")
        var fileInfo = stat()
        if lstat(target.path, &fileInfo) == 0 {
            guard (fileInfo.st_mode & S_IFMT) == S_IFREG, fileInfo.st_uid == getuid(),
                  fileInfo.st_nlink == 1, (fileInfo.st_mode & 0o022) == 0
            else { throw AgentAccountSetupError.unsafeDirectory }
        } else if errno != ENOENT { throw AgentAccountSetupError.unsafeDirectory }
        // All account UI windows share one manager. A cooperating second
        // process must not open a second writer over the same JSON snapshot.
        let lockPath = directory.appendingPathComponent(".registry.lock").path
        let fd = Darwin.open(lockPath, O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC, mode_t(0o600))
        guard fd >= 0 else { throw AgentAccountSetupError.unsafeDirectory }
        var lockInfo = stat()
        guard fstat(fd, &lockInfo) == 0, (lockInfo.st_mode & S_IFMT) == S_IFREG,
              lockInfo.st_uid == getuid(), lockInfo.st_nlink == 1,
              (lockInfo.st_mode & 0o077) == 0 else {
            _ = Darwin.close(fd)
            throw AgentAccountSetupError.unsafeDirectory
        }
        guard flock(fd, LOCK_EX | LOCK_NB) == 0 else {
            _ = Darwin.close(fd)
            throw AgentAccountSetupError.registryInUse
        }
        do {
            let opened = try AgentRegistryStore(directory:directory)
            lockFD = fd
            store = opened
            return opened
        } catch {
            _ = Darwin.close(fd)
            throw error
        }
    }

    public func create(providerID: String, mode: AgentConnectionMode, displayName: String,
                       modelID: String?) async throws -> AgentProfile {
        let name = displayName.trimmingCharacters(in:.whitespacesAndNewlines)
        let model = modelID?.trimmingCharacters(in:.whitespacesAndNewlines)
        guard !name.isEmpty, name.utf8.count <= 120,
              !name.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }),
              AgentProviderCatalog.candidates.contains(where: { $0.id == providerID && $0.modes.contains(mode) })
        else { throw AgentAccountSetupError.invalidConfiguration }
        // API model IDs must be explicit and match the current adapter contract.
        if mode == .officialAPI {
            guard let model, model.range(of:"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",options:.regularExpression) != nil
            else { throw AgentAccountSetupError.invalidConfiguration }
        } else if let model, !model.isEmpty {
            // Native model selection must be verified by a real browser driver.
            throw AgentAccountSetupError.invalidConfiguration
        }
        let id = UUID()
        let profile = AgentProfile(id:id,providerID:providerID,displayName:name,mode:mode,
            modelID:mode == .officialAPI ? model : nil,
            credentialReference:.forProfile(id:id,mode:mode),
            contextBoundary:mode == .officialAPI ? .packWorkspace : .providerAccount,
            memoryBoundary:mode == .officialAPI ? .disabled : .providerManaged,
            enabledCapabilities:[.chat])
        // Metadata first, then separate explicit credential setup. A failed
        // Keychain insert cannot leave an untracked secret or pretend success.
        try await registry().insert(profile)
        return profile
    }

    public func list() async throws -> [AgentAccountSummary] {
        let profiles = try await registry().list()
        var summaries: [AgentAccountSummary] = []
        for profile in profiles {
            let status: AgentLocalCredentialState
            if profile.mode == .nativeAccount { status = .nativeSessionUnverified }
            else {
                do { _ = try await vault.read(for:profile); status = .apiKeyPresentUnverified }
                catch AgentVaultError.missing { status = .apiKeyMissing }
                catch { status = .apiKeyUnavailable }
            }
            summaries.append(.init(profile:profile,credentialState:status))
        }
        return summaries
    }

    /// Uses the same process-owned registry; never opens a second snapshot.
    public func manualAPIHost(hub: any AgentCollaborationHub, destination: AgentExecutionDestination,
                              checkLease: Bool = false,
                              instance: AgentHubInstance? = nil) async throws -> AgentAPICollaborationHost {
        let registry = try registry()
        guard destination.accessKind == "api",
              case .apiCredentialPresent = try await AgentLocalAccountResolver(registry:registry,vault:vault).resolve(destination)
        else { throw AgentAccountSetupError.invalidConfiguration }
        try checkDirectory(directory.path,privateOnly:true)
        let root = directory.appendingPathComponent("execution-leases",isDirectory:true)
        if mkdir(root.path,0o700) != 0 && errno != EEXIST { throw AgentAccountSetupError.unsafeDirectory }
        try checkDirectory(root.path,privateOnly:true)
        if checkLease {
            let lease = try AgentSharedAccountLease(root:root,accountRef:destination.accountRef)
            try lease.release()
        }
        let admission: AgentCollaborationAdmission
        if let instance {
            guard instance.isValid else { throw AgentAccountSetupError.invalidConfiguration }
            if let existing = admissions[instance.instanceId] {
                admission = existing
            } else {
                // Bound memory without evicting unresolved Hub histories.
                guard admissions.count < 32 else { throw AgentAccountSetupError.invalidConfiguration }
                admission = .init()
                admissions[instance.instanceId] = admission
            }
        } else {
            // Compatibility for callers without a verified instance. They
            // receive only host-local admission, never a guessed shared scope.
            admission = .init()
        }
        return AgentAPICollaborationHost(hub:hub,registry:registry,vault:vault,
                                         leaseRoot:root,expected:destination,admission:admission)
    }

    /// Never overwrite, rotate, export or delete an existing key here.
    public func configureMissingAPIKey(id: UUID, key: String) async throws {
        let profile = try await registry().profile(id:id)
        guard profile.mode == .officialAPI else { throw AgentAccountSetupError.invalidConfiguration }
        try await vault.insert(key,for:profile)
    }
}
