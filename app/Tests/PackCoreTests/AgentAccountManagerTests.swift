import XCTest
@testable import PackCore

private actor SetupBackend: AgentSecretBackend {
    private var data:[String:Data] = [:]
    func insert(account:String,secret:Data) async throws {
        guard data[account] == nil else { throw AgentVaultError.duplicate }
        data[account] = secret
    }
    func read(account:String) async throws -> Data {
        guard let value = data[account] else { throw AgentVaultError.missing }
        return value
    }
    func replace(account:String,secret:Data) async throws { throw AgentVaultError.invalidReference }
    func remove(account:String) async throws { throw AgentVaultError.invalidReference }
}
final class AgentAccountManagerTests:XCTestCase {
    func testSeparateProfilesAndInsertOnlySecret() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString,isDirectory:true)
        try FileManager.default.createDirectory(at:root,withIntermediateDirectories:false,attributes:[.posixPermissions:0o700])
        defer { try? FileManager.default.removeItem(at:root) }
        let dir = root.appendingPathComponent("agents",isDirectory:true)
        let manager = AgentAccountManager(directory:dir,vault:.init(backend:SetupBackend()))
        let native = try await manager.create(providerID:"chatgpt",mode:.nativeAccount,displayName:"Personal",modelID:nil)
        let api = try await manager.create(providerID:"openai",mode:.officialAPI,displayName:"API",modelID:"test-model")
        XCTAssertNotEqual(native.id,api.id)
        XCTAssertEqual(native.memoryBoundary,.providerManaged)
        XCTAssertEqual(api.memoryBoundary,.disabled)
        let before = try await manager.list()
        XCTAssertEqual(before.first(where:{$0.id == api.id})?.credentialState,.apiKeyMissing)
        try await manager.configureMissingAPIKey(id:api.id,key:"test-secret-value")
        let after = try await manager.list()
        XCTAssertEqual(after.first(where:{$0.id == api.id})?.credentialState,.apiKeyPresentUnverified)
        XCTAssertEqual(after.first(where:{$0.id == native.id})?.credentialState,.nativeSessionUnverified)
        let json = try String(contentsOf:dir.appendingPathComponent("agent-registry.json"),encoding:.utf8)
        XCTAssertFalse(json.contains("test-secret-value"))
        do { try await manager.configureMissingAPIKey(id:api.id,key:"replacement"); XCTFail("must not overwrite") }
        catch { XCTAssertEqual(error as? AgentVaultError,.duplicate) }
        do { try await manager.configureMissingAPIKey(id:native.id,key:"native-secret"); XCTFail("native rejected") }
        catch { XCTAssertEqual(error as? AgentAccountSetupError,.invalidConfiguration) }
        let another = AgentAccountManager(directory:dir)
        do { _ = try await another.list(); XCTFail("second writer must fail") }
        catch { XCTAssertEqual(error as? AgentAccountSetupError,.registryInUse) }
    }
    func testUnsafeSymlinkDirectoryFails() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString,isDirectory:true)
        try FileManager.default.createDirectory(at:root,withIntermediateDirectories:false,attributes:[.posixPermissions:0o700])
        defer { try? FileManager.default.removeItem(at:root) }
        let alias = root.appendingPathComponent("alias")
        try FileManager.default.createSymbolicLink(at:alias,withDestinationURL:root)
        let manager = AgentAccountManager(directory:alias)
        do { _ = try await manager.list(); XCTFail("symlink rejected") }
        catch { XCTAssertEqual(error as? AgentAccountSetupError,.unsafeDirectory) }
    }
}
