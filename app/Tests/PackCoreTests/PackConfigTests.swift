import Foundation
import XCTest
@testable import PackCore

private final class FakeCredentialStore: CredentialStore {
    var entries: [String: String] = [:]
    var readOverrides: [String: CredentialReadResult] = [:]
    var addStatus: Int32 = 0

    func read(service: String, account: String) -> CredentialReadResult {
        if let override = readOverrides[account] { return override }
        guard let value = entries[account] else { return .notFound }
        return .found(value)
    }

    @discardableResult
    func add(service: String, account: String, value: String) -> Int32 {
        guard addStatus == 0 else { return addStatus }
        entries[account] = value
        return 0
    }
}

final class PackConfigTests: XCTestCase {
    private let service = "com.cyou.pack.tests"

    private func makeConfig(_ object: [String: Any], permissions: Int = 0o644) throws -> (directory: URL, file: URL) {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("PackConfigTests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let file = directory.appendingPathComponent("config.json")
        let data = try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
        guard FileManager.default.createFile(
            atPath: file.path,
            contents: data,
            attributes: [.posixPermissions: permissions]
        ) else {
            throw CocoaError(.fileWriteUnknown)
        }
        return (directory, file)
    }

    private func readConfig(_ file: URL) throws -> [String: Any] {
        let data = try Data(contentsOf: file)
        return try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
    }

    private func permissions(_ file: URL) throws -> Int {
        let attributes = try FileManager.default.attributesOfItem(atPath: file.path)
        return try XCTUnwrap((attributes[.posixPermissions] as? NSNumber)?.intValue)
    }

    func testMigratesBothCredentialsAndRemovesPlaintextIdempotently() throws {
        let fixture = try makeConfig([
            "hubUrl": "https://hub.example.com/mcp",
            "token": "dispatcher-secret",
            "ownerToken": "owner-secret"
        ])
        defer { try? FileManager.default.removeItem(at: fixture.directory) }
        let store = FakeCredentialStore()

        let first = PackConfig.load(path: fixture.file, service: service, credentialStore: store)
        XCTAssertEqual(first.config?.token, "dispatcher-secret")
        XCTAssertEqual(first.config?.ownerToken, "owner-secret")
        XCTAssertEqual(store.entries["dispatcher"], "dispatcher-secret")
        XCTAssertEqual(store.entries["owner"], "owner-secret")

        let cleaned = try readConfig(fixture.file)
        XCTAssertNil(cleaned["token"])
        XCTAssertNil(cleaned["ownerToken"])
        XCTAssertEqual(cleaned["hubUrl"] as? String, "https://hub.example.com/mcp")
        XCTAssertEqual(try permissions(fixture.file) & 0o077, 0)

        let second = PackConfig.load(path: fixture.file, service: service, credentialStore: store)
        XCTAssertEqual(second.config?.token, "dispatcher-secret")
        XCTAssertEqual(second.config?.ownerToken, "owner-secret")
    }

    func testExistingMatchingKeychainCredentialRemovesPlaintextCopy() throws {
        let fixture = try makeConfig([
            "hubUrl": "https://hub.example.com/mcp",
            "token": "dispatcher-secret"
        ])
        defer { try? FileManager.default.removeItem(at: fixture.directory) }
        let store = FakeCredentialStore()
        store.entries["dispatcher"] = "dispatcher-secret"

        let result = PackConfig.load(path: fixture.file, service: service, credentialStore: store)

        XCTAssertEqual(result.config?.token, "dispatcher-secret")
        XCTAssertNil(try readConfig(fixture.file)["token"])
    }

    func testKeychainReadFailureDoesNotFallBackToPlaintextToken() throws {
        let fixture = try makeConfig([
            "hubUrl": "https://hub.example.com/mcp",
            "token": "plaintext-must-not-be-used"
        ])
        defer { try? FileManager.default.removeItem(at: fixture.directory) }
        let store = FakeCredentialStore()
        store.readOverrides["dispatcher"] = .failure(-25291)

        let result = PackConfig.load(path: fixture.file, service: service, credentialStore: store)

        XCTAssertNil(result.config)
        XCTAssertTrue(result.credentialStatus.contains("已阻止回退"))
        XCTAssertEqual(try readConfig(fixture.file)["token"] as? String, "plaintext-must-not-be-used")
    }

    func testKeychainAndFileCredentialMismatchFailsClosed() throws {
        let fixture = try makeConfig([
            "hubUrl": "https://hub.example.com/mcp",
            "token": "old-file-token"
        ])
        defer { try? FileManager.default.removeItem(at: fixture.directory) }
        let store = FakeCredentialStore()
        store.entries["dispatcher"] = "different-keychain-token"

        let result = PackConfig.load(path: fixture.file, service: service, credentialStore: store)

        XCTAssertNil(result.config)
        XCTAssertTrue(result.credentialStatus.contains("不一致"))
        XCTAssertEqual(try readConfig(fixture.file)["token"] as? String, "old-file-token")
    }

    func testInvalidKeychainDataFailsClosed() throws {
        let fixture = try makeConfig([
            "hubUrl": "https://hub.example.com/mcp",
            "token": "plaintext-must-not-be-used"
        ])
        defer { try? FileManager.default.removeItem(at: fixture.directory) }
        let store = FakeCredentialStore()
        store.readOverrides["dispatcher"] = .invalidData

        let result = PackConfig.load(path: fixture.file, service: service, credentialStore: store)

        XCTAssertNil(result.config)
        XCTAssertTrue(result.credentialStatus.contains("数据无效"))
    }

    func testFailedKeychainWriteDoesNotReturnPlaintextCredential() throws {
        let fixture = try makeConfig([
            "hubUrl": "https://hub.example.com/mcp",
            "token": "plaintext-must-not-be-used"
        ])
        defer { try? FileManager.default.removeItem(at: fixture.directory) }
        let store = FakeCredentialStore()
        store.addStatus = -25291

        let result = PackConfig.load(path: fixture.file, service: service, credentialStore: store)

        XCTAssertNil(result.config)
        XCTAssertTrue(result.credentialStatus.contains("写入未生效"))
        XCTAssertEqual(try readConfig(fixture.file)["token"] as? String, "plaintext-must-not-be-used")
    }

    func testOwnerCredentialReadFailureBlocksPartialConfiguration() throws {
        let fixture = try makeConfig([
            "hubUrl": "https://hub.example.com/mcp"
        ])
        defer { try? FileManager.default.removeItem(at: fixture.directory) }
        let store = FakeCredentialStore()
        store.entries["dispatcher"] = "dispatcher-secret"
        store.readOverrides["owner"] = .failure(-25291)

        let result = PackConfig.load(path: fixture.file, service: service, credentialStore: store)

        XCTAssertNil(result.config)
        XCTAssertTrue(result.credentialStatus.contains("已阻止回退"))
    }

    func testMissingDispatcherCredentialDoesNotCreateConfiguration() throws {
        let fixture = try makeConfig(["hubUrl": "https://hub.example.com/mcp"])
        defer { try? FileManager.default.removeItem(at: fixture.directory) }
        let store = FakeCredentialStore()

        let result = PackConfig.load(path: fixture.file, service: service, credentialStore: store)

        XCTAssertNil(result.config)
        XCTAssertTrue(result.credentialStatus.contains("未找到调度员凭证"))
    }

    func testInsecureConfigPermissionsAreRestrictedBeforeUse() throws {
        let fixture = try makeConfig([
            "hubUrl": "https://hub.example.com/mcp",
            "token": "dispatcher-secret"
        ], permissions: 0o644)
        defer { try? FileManager.default.removeItem(at: fixture.directory) }
        let store = FakeCredentialStore()

        _ = PackConfig.load(path: fixture.file, service: service, credentialStore: store)

        XCTAssertEqual(try permissions(fixture.file) & 0o077, 0)
    }

    func testInvalidHubURLPreventsCredentialLoading() throws {
        let fixture = try makeConfig([
            "hubUrl": "http://hub.example.com/mcp",
            "token": "dispatcher-secret"
        ])
        defer { try? FileManager.default.removeItem(at: fixture.directory) }
        let store = FakeCredentialStore()

        let result = PackConfig.load(path: fixture.file, service: service, credentialStore: store)

        XCTAssertNil(result.config)
        XCTAssertTrue(store.entries.isEmpty)
    }
}
