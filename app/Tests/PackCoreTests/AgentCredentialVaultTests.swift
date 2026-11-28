import Foundation
import XCTest
@testable import PackCore

private actor MemorySecretBackend: AgentSecretBackend {
    private var values: [String: Data] = [:]
    private let afterInsert: (@Sendable () async -> Void)?
    private let failRemoval: Bool

    init(afterInsert: (@Sendable () async -> Void)? = nil, failRemoval: Bool = false) {
        self.afterInsert = afterInsert
        self.failRemoval = failRemoval
    }
    func insert(account: String, secret: Data) async throws {
        guard values[account] == nil else { throw AgentVaultError.duplicate }
        values[account] = secret
        await afterInsert?()
    }
    func read(account: String) async throws -> Data {
        guard let value = values[account] else { throw AgentVaultError.missing }
        return value
    }
    func replace(account: String, secret: Data) async throws {
        guard values[account] != nil else { throw AgentVaultError.missing }
        values[account] = secret
    }
    func remove(account: String) async throws {
        if failRemoval { throw AgentVaultError.keychainFailure(-1) }
        guard values.removeValue(forKey: account) != nil else { throw AgentVaultError.missing }
    }
}

final class AgentCredentialVaultTests: XCTestCase {
    private func profile(_ provider: String = "gemini-api", id: UUID = UUID(),
                         mode: AgentConnectionMode = .officialAPI) -> AgentProfile {
        AgentProfile(id: id, providerID: provider, displayName: provider, mode: mode,
                     credentialReference: .forProfile(id: id, mode: mode),
                     contextBoundary: mode == .officialAPI ? .packWorkspace : .providerAccount,
                     memoryBoundary: mode == .officialAPI ? .packManaged : .providerManaged)
    }

    func testGeminiModesAndMetadataContainsOnlyReference() throws {
        let api = profile()
        let native = profile("gemini", mode: .nativeAccount)
        var registry = AgentRegistry()
        try registry.insert(api)
        try registry.insert(native)
        let restored = try AgentRegistry.decode(registry.encoded())
        XCTAssertEqual(restored.profiles, registry.profiles)
        XCTAssertThrowsError(try registry.insert(profile("gemini")))
        XCTAssertThrowsError(try registry.insert(profile("gemini-api", mode: .nativeAccount)))
        XCTAssertEqual(api.credentialReference?.vaultKey, api.id.uuidString.lowercased())
    }

    func testSecretLifecycleAndCrossProviderIsolation() async throws {
        let vault = AgentCredentialVault(backend: MemorySecretBackend())
        let api = profile(), second = profile("openai", id: api.id)
        let third = profile()
        try await vault.insert("fixture-one", for: api)
        try await vault.insert("fixture-two", for: second)
        try await vault.insert("fixture-three", for: third)
        do {
            try await vault.insert("fixture-overwrite", for: api)
            XCTFail("duplicate insert must fail")
        } catch { XCTAssertEqual(error as? AgentVaultError, .duplicate) }
        try await vault.replace("fixture-rotated", for: api)
        let firstValue = try await vault.read(for: api)
        let secondValue = try await vault.read(for: second)
        XCTAssertEqual(firstValue, "fixture-rotated")
        XCTAssertEqual(secondValue, "fixture-two")
        try await vault.remove(for: api)
        do {
            _ = try await vault.read(for: api)
            XCTFail("removed key must not be readable")
        } catch { XCTAssertEqual(error as? AgentVaultError, .missing) }
        let thirdValue = try await vault.read(for: third)
        XCTAssertEqual(thirdValue, "fixture-three")
    }

    func testRefusesNativeSessionInvalidReferenceAndInvalidSecret() async throws {
        let vault = AgentCredentialVault(backend: MemorySecretBackend())
        let native = profile("gemini", mode: .nativeAccount)
        do {
            try await vault.insert("fixture", for: native)
            XCTFail("native account must not use API vault")
        } catch { XCTAssertEqual(error as? AgentVaultError, .invalidReference) }
        var invalid = profile()
        invalid.credentialReference = .init(kind: .apiKey, vaultKey: UUID().uuidString)
        do {
            try await vault.insert("fixture", for: invalid)
            XCTFail("arbitrary reference must fail")
        } catch { XCTAssertEqual(error as? AgentVaultError, .invalidReference) }
        do {
            try await vault.insert("bad key", for: profile())
            XCTFail("invalid key must fail")
        } catch { XCTAssertEqual(error as? AgentVaultError, .invalidSecret) }
    }

    private func privateDirectory() throws -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o700])
        return url
    }

    func testProvisionPersistsProfileAndCredentialWithoutSecretInMetadata() async throws {
        let directory = try privateDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = try AgentRegistryStore(directory: directory)
        let vault = AgentCredentialVault(backend: MemorySecretBackend())
        let api = profile()
        try await store.provisionAPIProfile(api, apiKey: "fixture-provision", vault: vault)
        let reopened = try AgentRegistryStore(directory: directory)
        let profiles = await reopened.list()
        let key = try await vault.read(for: api)
        XCTAssertEqual(profiles, [api])
        XCTAssertEqual(key, "fixture-provision")
        let metadata = try String(contentsOf: directory.appendingPathComponent("agent-registry.json"),
                                  encoding: .utf8)
        XCTAssertFalse(metadata.contains("fixture-provision"))
        do {
            try await store.provisionAPIProfile(api, apiKey: "fixture-overwrite", vault: vault)
            XCTFail("duplicate profile must fail")
        } catch { XCTAssertEqual(error as? AgentRegistryError, .duplicateProfile) }
        let unchanged = try await vault.read(for: api)
        XCTAssertEqual(unchanged, "fixture-provision")
    }

    func testProvisionRollsBackCredentialOnMetadataFailure() async throws {
        let directory = try privateDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = try AgentRegistryStore(directory: directory)
        let vault = AgentCredentialVault(backend: MemorySecretBackend())
        let api = profile()
        // A directory at the registry file path makes persistence fail safely.
        try FileManager.default.createDirectory(
            at: directory.appendingPathComponent("agent-registry.json"),
            withIntermediateDirectories: false)
        do {
            try await store.provisionAPIProfile(api, apiKey: "fixture-rollback", vault: vault)
            XCTFail("metadata write must fail")
        } catch { XCTAssertEqual(error as? AgentRegistryError, .unsafeStorage) }
        let profiles = await store.list()
        XCTAssertTrue(profiles.isEmpty)
        do {
            _ = try await vault.read(for: api)
            XCTFail("new credential must be rolled back")
        } catch { XCTAssertEqual(error as? AgentVaultError, .missing) }
    }

    func testProvisionDoesNotRemovePreexistingCredential() async throws {
        let directory = try privateDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = try AgentRegistryStore(directory: directory)
        let vault = AgentCredentialVault(backend: MemorySecretBackend())
        let api = profile()
        try await vault.insert("fixture-existing", for: api)
        do {
            try await store.provisionAPIProfile(api, apiKey: "fixture-new", vault: vault)
            XCTFail("existing credential must not be overwritten")
        } catch { XCTAssertEqual(error as? AgentVaultError, .duplicate) }
        let key = try await vault.read(for: api)
        let profiles = await store.list()
        XCTAssertEqual(key, "fixture-existing")
        XCTAssertTrue(profiles.isEmpty)
    }

    func testRotationRequiresRegisteredAPIProfileAndExistingCredential() async throws {
        let directory = try privateDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = try AgentRegistryStore(directory: directory)
        let vault = AgentCredentialVault(backend: MemorySecretBackend())
        let api = profile(), native = profile("gemini", mode: .nativeAccount)
        let missing = profile("openai")
        try await store.provisionAPIProfile(api, apiKey: "fixture-original", vault: vault)
        try await store.insert(native)
        try await store.insert(missing)
        let before = await store.list()

        try await store.rotateAPICredential(id: api.id, apiKey: "fixture-rotated", vault: vault)
        let rotated = try await vault.read(for: api)
        XCTAssertEqual(rotated, "fixture-rotated")
        do {
            try await store.rotateAPICredential(id: UUID(), apiKey: "fixture", vault: vault)
            XCTFail("unknown profile must fail")
        } catch { XCTAssertEqual(error as? AgentRegistryError, .unknownProfile) }
        do {
            try await store.rotateAPICredential(id: native.id, apiKey: "fixture", vault: vault)
            XCTFail("native profile must fail")
        } catch { XCTAssertEqual(error as? AgentRegistryError, .unsupportedMode) }
        do {
            try await store.rotateAPICredential(id: missing.id, apiKey: "fixture", vault: vault)
            XCTFail("rotation must not insert a missing credential")
        } catch { XCTAssertEqual(error as? AgentVaultError, .missing) }
        do {
            try await store.rotateAPICredential(id: api.id, apiKey: "invalid key", vault: vault)
            XCTFail("invalid rotation must fail")
        } catch { XCTAssertEqual(error as? AgentVaultError, .invalidSecret) }
        let unchanged = try await vault.read(for: api)
        let after = await store.list()
        XCTAssertEqual(unchanged, "fixture-rotated")
        XCTAssertEqual(before, after)
    }

    func testProvisionRejectsReentrantMetadataMutation() async throws {
        let directory = try privateDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = try AgentRegistryStore(directory: directory)
        let api = profile(), other = profile("gemini", mode: .nativeAccount)
        let backend = MemorySecretBackend(afterInsert: {
            do {
                try await store.insert(other)
                XCTFail("concurrent metadata insertion must fail")
            } catch { XCTAssertEqual(error as? AgentProvisioningError, .operationInProgress) }
        })
        let vault = AgentCredentialVault(backend: backend)
        try await store.provisionAPIProfile(api, apiKey: "fixture", vault: vault)
        let during = await store.list()
        XCTAssertEqual(during, [api])
        // The operation gate must release on success.
        try await store.insert(other)
        let after = await store.list()
        XCTAssertEqual(Set(after.map { $0.id }), Set([api.id, other.id]))
    }

    func testProvisionReportsFailedRollbackWithoutPublishingMetadata() async throws {
        let directory = try privateDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = try AgentRegistryStore(directory: directory)
        let vault = AgentCredentialVault(backend: MemorySecretBackend(failRemoval: true))
        let api = profile()
        let path = directory.appendingPathComponent("agent-registry.json")
        try FileManager.default.createDirectory(at: path, withIntermediateDirectories: false)
        do {
            try await store.provisionAPIProfile(api, apiKey: "fixture-orphan", vault: vault)
            XCTFail("failed rollback must be reported")
        } catch {
            XCTAssertEqual(error as? AgentProvisioningError, .credentialCleanupRequired(profileID: api.id))
        }
        let profiles = await store.list()
        let retained = try await vault.read(for: api)
        XCTAssertTrue(profiles.isEmpty)
        XCTAssertEqual(retained, "fixture-orphan")
        // Failed rollback must not permanently lock unrelated metadata operations.
        try FileManager.default.removeItem(at: path)
        try await store.insert(profile("gemini", mode: .nativeAccount))
    }
}
