import Foundation
import XCTest
@testable import PackCore

final class AgentConnectionReportTests: XCTestCase {
    private struct Row: Decodable {
        let providerID: String
        let mode: AgentConnectionMode
        let stage: AgentIntegrationStage
        let implementedCapabilities: [String]
    }

    func testSharedIntegrationStagesNeverGrantExecution() throws {
        let root = URL(fileURLWithPath:#filePath)
            .deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
        let fixture = root.appendingPathComponent("tests/fixtures/agent-integration-stages.json")
        let rows = try JSONDecoder().decode([Row].self,from:Data(contentsOf:fixture))
        for row in rows {
            let id = UUID()
            let api = row.mode == .officialAPI
            let profile = AgentProfile(id:id,providerID:row.providerID,displayName:"fixture",mode:row.mode,
                modelID:api ? "fixture-model" : nil,credentialReference:.forProfile(id:id,mode:row.mode),
                contextBoundary:api ? .packWorkspace : .providerAccount,
                memoryBoundary:api ? .disabled : .providerManaged,enabledCapabilities:[.chat,.toolCalling])
            let report = AgentConnectionReport(profile:profile,
                credentialState:api ? .apiKeyPresentUnverified : .nativeSessionUnverified)
            XCTAssertEqual(report.stage,row.stage,row.providerID)
            XCTAssertEqual(report.implementedCapabilities,row.implementedCapabilities)
            XCTAssertEqual(report.configuredCapabilities,["chat","toolCalling"])
            XCTAssertTrue(report.verifiedCapabilities.isEmpty)
            XCTAssertFalse(report.identityVerified)
            XCTAssertFalse(report.executionVerified)
            XCTAssertFalse(report.automaticExecutionEnabled)
        }
    }

    func testConfiguredKeyIsNotRemoteCapabilityProof() {
        let id = UUID()
        let profile = AgentProfile(id:id,providerID:"openai",displayName:"fixture",mode:.officialAPI,
            modelID:"fixture-model",credentialReference:.forProfile(id:id,mode:.officialAPI),
            contextBoundary:.packWorkspace,memoryBoundary:.disabled)
        let missing = AgentConnectionReport(profile:profile,credentialState:.apiKeyMissing)
        let present = AgentConnectionReport(profile:profile,credentialState:.apiKeyPresentUnverified)
        XCTAssertNotEqual(missing.credentialState,present.credentialState)
        XCTAssertEqual(missing.implementedCapabilities,present.implementedCapabilities)
        XCTAssertEqual(present.verifiedCapabilities,[])
        XCTAssertFalse(present.executionVerified)
    }
}
