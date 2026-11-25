import Foundation
import XCTest
@testable import PackCore

private actor ResolverSecretBackend: AgentSecretBackend {
    private var values: [String: Data] = [:]
    func insert(account: String, secret: Data) async throws { values[account] = secret }
    func read(account: String) async throws -> Data {
        guard let data = values[account] else { throw AgentVaultError.missing }
        return data
    }
    func replace(account: String, secret: Data) async throws { values[account] = secret }
    func remove(account: String) async throws { values.removeValue(forKey: account) }
}

final class AgentLocalAccountResolverTests: XCTestCase {
    private func directory() throws -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: false,
            attributes: [.posixPermissions: 0o700])
        addTeardownBlock { try FileManager.default.removeItem(at: url) }
        return url
    }

    private func profile(provider: String, mode: AgentConnectionMode) -> AgentProfile {
        let id = UUID()
        return AgentProfile(id: id, providerID: provider, displayName: provider, mode: mode,
            credentialReference: .forProfile(id: id, mode: mode),
            contextBoundary: mode == .officialAPI ? .packWorkspace : .providerAccount,
            memoryBoundary: mode == .officialAPI ? .disabled : .providerManaged)
    }

    func testExistingAPIKeyPresenceIsNotLoginVerification() async throws {
        let registry = try AgentRegistryStore(directory: directory())
        let vault = AgentCredentialVault(backend: ResolverSecretBackend())
        let api = profile(provider: "openai", mode: .officialAPI)
        try await registry.insert(api)
        let destination = AgentExecutionDestination(accountRef: api.id.uuidString.lowercased(),
            provider: "openai", accessKind: "api")
        let resolver = AgentLocalAccountResolver(registry: registry, vault: vault)
        do {
            _ = try await resolver.resolve(destination)
            XCTFail("missing credential accepted")
        } catch { XCTAssertEqual(error as? AgentVaultError, .missing) }
        try await vault.insert("fixture-key", for: api)
        let resolved = try await resolver.resolve(destination)
        XCTAssertEqual(resolved, .apiCredentialPresent(profileID: api.id))
        try await registry.remove(id: api.id)
        do {
            _ = try await resolver.resolve(destination)
            XCTFail("removed profile accepted")
        } catch { XCTAssertEqual(error as? AgentRegistryError, .unknownProfile) }
    }

    func testNativeRequiresBrowserVerificationAndRejectsCrossProvider() async throws {
        let registry = try AgentRegistryStore(directory: directory())
        let native = profile(provider: "chatgpt", mode: .nativeAccount)
        try await registry.insert(native)
        let resolver = AgentLocalAccountResolver(registry: registry,
            vault: AgentCredentialVault(backend: ResolverSecretBackend()))
        let id = native.id.uuidString.lowercased()
        let result = try await resolver.resolve(.init(accountRef: id, provider: "chatgpt", accessKind: "native"))
        XCTAssertEqual(result, .nativeSessionRequired(profileID: native.id))
        for destination in [
            AgentExecutionDestination(accountRef: id, provider: "gemini", accessKind: "native"),
            AgentExecutionDestination(accountRef: id, provider: "chatgpt", accessKind: "api"),
            AgentExecutionDestination(accountRef: id, provider: "chatgpt", accessKind: "unknown")
        ] {
            do {
                _ = try await resolver.resolve(destination)
                XCTFail("mismatched binding accepted")
            } catch { XCTAssertEqual(error as? AgentAccountResolutionError, .identityMismatch) }
        }
    }
}
