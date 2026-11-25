import Foundation
import XCTest
@testable import PackCore

final class AgentRegistryTests: XCTestCase {
    private func native(id: UUID = UUID(), key: String = UUID().uuidString) -> AgentProfile {
        AgentProfile(id: id, providerID: "chatgpt", displayName: "Personal",
                     mode: .nativeAccount,
                     credentialReference: .init(kind: .nativeSession, vaultKey: key),
                     contextBoundary: .providerAccount, memoryBoundary: .providerManaged)
    }

    private func api(id: UUID = UUID(), key: String = UUID().uuidString) -> AgentProfile {
        AgentProfile(id: id, providerID: "openai", displayName: "API",
                     mode: .officialAPI,
                     credentialReference: .init(kind: .apiKey, vaultKey: key),
                     contextBoundary: .packWorkspace, memoryBoundary: .packManaged)
    }

    func testRejectDuplicateIdentityAndCredentialReuse() throws {
        var registry = AgentRegistry()
        let one = native(key: "shared-reference")
        try registry.insert(one)
        XCTAssertThrowsError(try registry.insert(one))
        XCTAssertThrowsError(try registry.insert(api(key: "shared-reference"))) {
            XCTAssertEqual($0 as? AgentRegistryError, .credentialAlreadyInUse)
        }
        XCTAssertEqual(registry.profiles, [one])
    }

    func testProviderModeAndImmutableIdentity() throws {
        var registry = AgentRegistry()
        var one = native()
        try registry.insert(one)
        one.displayName = "Renamed"
        try registry.update(one)
        XCTAssertEqual(registry.profiles, [one])
        XCTAssertThrowsError(try registry.update(api(id: one.id))) {
            XCTAssertEqual($0 as? AgentRegistryError, .immutableIdentity)
        }
        var invalid = api()
        invalid.providerID = "chatgpt"
        XCTAssertThrowsError(try registry.insert(invalid)) {
            XCTAssertEqual($0 as? AgentRegistryError, .unsupportedMode)
        }
        invalid.providerID = "unknown"
        XCTAssertThrowsError(try registry.insert(invalid)) {
            XCTAssertEqual($0 as? AgentRegistryError, .unknownProvider)
        }
    }

    func testProfilesRoundTripAndIndependentNativeDirectories() throws {
        var registry = AgentRegistry()
        let one = native(), two = native(), three = api()
        for profile in [one, two, three] { try registry.insert(profile) }
        let restored = try AgentRegistry.decode(registry.encoded())
        XCTAssertEqual(restored.profiles, registry.profiles)
        XCTAssertNotEqual(try restored.nativeSessionDirectoryName(id: one.id),
                          try restored.nativeSessionDirectoryName(id: two.id))
        XCTAssertThrowsError(try restored.nativeSessionDirectoryName(id: three.id))
        let invalid = Data("{\"schemaVersion\":2,\"profiles\":[]}".utf8)
        XCTAssertThrowsError(try AgentRegistry.decode(invalid)) {
            XCTAssertEqual($0 as? AgentRegistryError, .unsupportedSchema)
        }
    }

    func testStoreRejectsSymlinkAndNonPrivateDirectory() throws {
        let fm = FileManager.default
        let directory = fm.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try fm.createDirectory(at: directory, withIntermediateDirectories: false,
                               attributes: [.posixPermissions: 0o755])
        defer { try? fm.removeItem(at: directory) }
        XCTAssertThrowsError(try AgentRegistryStore(directory: directory)) {
            XCTAssertEqual($0 as? AgentRegistryError, .unsafeStorage)
        }
        try fm.setAttributes([.posixPermissions: 0o700], ofItemAtPath: directory.path)
        let external = directory.appendingPathComponent("other.json")
        try Data("preserve".utf8).write(to: external)
        let target = directory.appendingPathComponent("agent-registry.json")
        try fm.createSymbolicLink(at: target, withDestinationURL: external)
        XCTAssertThrowsError(try AgentRegistryStore(directory: directory)) {
            XCTAssertEqual($0 as? AgentRegistryError, .unsafeStorage)
        }
        XCTAssertEqual(try Data(contentsOf: external), Data("preserve".utf8))
    }

    func testStorePersistsAndRejectsCorruptSnapshot() async throws {
        let fm = FileManager.default
        let directory = fm.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try fm.createDirectory(at: directory, withIntermediateDirectories: false,
                               attributes: [.posixPermissions: 0o700])
        defer { try? fm.removeItem(at: directory) }
        let one = native()
        let store = try AgentRegistryStore(directory: directory)
        try await store.insert(one)
        let reopened = try AgentRegistryStore(directory: directory)
        let profiles = await reopened.list()
        XCTAssertEqual(profiles, [one])
        try await reopened.remove(id: one.id)
        let third = try AgentRegistryStore(directory: directory)
        let empty = await third.list()
        XCTAssertTrue(empty.isEmpty)
        let file = directory.appendingPathComponent("agent-registry.json")
        let corrupt = Data("not JSON".utf8)
        try corrupt.write(to: file)
        XCTAssertThrowsError(try AgentRegistryStore(directory: directory))
        XCTAssertEqual(try Data(contentsOf: file), corrupt)
    }
}
