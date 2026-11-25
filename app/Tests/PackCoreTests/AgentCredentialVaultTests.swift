import Foundation
import XCTest
@testable import PackCore

private actor MemorySecretBackend: AgentSecretBackend {
    private var values: [String: Data] = [:]
    func insert(account: String, secret: Data) async throws {
        guard values[account] == nil else { throw AgentVaultError.duplicate }
        values[account] = secret
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
}
