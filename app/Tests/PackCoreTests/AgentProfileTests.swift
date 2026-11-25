import XCTest
@testable import PackCore

final class AgentProfileTests: XCTestCase {
    func testNativeAccountKeepsProviderContextAndMemoryBoundaries() throws {
        let profile = AgentProfile(
            providerID: "chatgpt",
            displayName: "ChatGPT · 个人账号",
            mode: .nativeAccount,
            accountLabel: "main",
            credentialReference: AgentCredentialReference(
                kind: .nativeSession, vaultKey: "native-session:chatgpt:main"),
            contextBoundary: .providerAccount,
            memoryBoundary: .providerManaged,
            enabledCapabilities: [.chat, .toolCalling, .browserAutomation]
        )
        XCTAssertNoThrow(try profile.validate())
    }

    func testOfficialAPIUsesPackWorkspaceAndPackManagedMemory() throws {
        let profile = AgentProfile(
            providerID: "doubao",
            displayName: "豆包 API",
            mode: .officialAPI,
            modelID: "configured-model",
            credentialReference: AgentCredentialReference(
                kind: .apiKey, vaultKey: "provider-api-key:doubao:primary"),
            contextBoundary: .packWorkspace,
            memoryBoundary: .packManaged,
            enabledCapabilities: [.chat, .toolCalling]
        )
        XCTAssertNoThrow(try profile.validate())
    }

    func testAPICannotClaimProviderPersonalMemory() {
        let profile = AgentProfile(
            providerID: "chatgpt",
            displayName: "ChatGPT API",
            mode: .officialAPI,
            credentialReference: AgentCredentialReference(
                kind: .apiKey, vaultKey: "provider-api-key:openai:primary"),
            contextBoundary: .providerAccount,
            memoryBoundary: .providerManaged
        )
        XCTAssertThrowsError(try profile.validate()) { error in
            XCTAssertEqual(error as? AgentProfileValidationError, .contextBoundaryMismatch)
        }
    }

    func testNativeAccountCannotUseAPIKeyReference() {
        let profile = AgentProfile(
            providerID: "doubao",
            displayName: "豆包个人账号",
            mode: .nativeAccount,
            credentialReference: AgentCredentialReference(
                kind: .apiKey, vaultKey: "provider-api-key:doubao:primary"),
            contextBoundary: .providerAccount,
            memoryBoundary: .providerManaged
        )
        XCTAssertThrowsError(try profile.validate()) { error in
            XCTAssertEqual(error as? AgentProfileValidationError, .credentialModeMismatch)
        }
    }

    func testProfilesRequireNonEmptyVaultReference() {
        let profile = AgentProfile(
            providerID: "openai",
            displayName: "OpenAI API",
            mode: .officialAPI,
            credentialReference: AgentCredentialReference(kind: .apiKey, vaultKey: "  "),
            contextBoundary: .packWorkspace,
            memoryBoundary: .packManaged
        )
        XCTAssertThrowsError(try profile.validate()) { error in
            XCTAssertEqual(error as? AgentProfileValidationError, .emptyCredentialReference)
        }
    }

    func testProfileCodableRoundTripDoesNotContainSecretMaterial() throws {
        let profile = AgentProfile(
            providerID: "openai",
            displayName: "OpenAI API",
            mode: .officialAPI,
            modelID: "model-id",
            credentialReference: AgentCredentialReference(
                kind: .apiKey, vaultKey: "keychain:openai:primary"),
            contextBoundary: .packWorkspace,
            memoryBoundary: .packManaged
        )
        let data = try JSONEncoder().encode(profile)
        let json = String(decoding: data, as: UTF8.self)
        XCTAssertFalse(json.contains("sk-"))
        XCTAssertTrue(json.contains("keychain:openai:primary"))
        XCTAssertEqual(try JSONDecoder().decode(AgentProfile.self, from: data), profile)
    }
}
