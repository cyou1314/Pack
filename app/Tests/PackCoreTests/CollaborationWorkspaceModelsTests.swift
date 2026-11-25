import XCTest
@testable import PackCore

final class CollaborationWorkspaceModelsTests: XCTestCase {
    func testWorkspaceWithUnboundStepAndUnpublishedFinal() throws {
        let data = Data(#"{"room":"pack-main","next_before_source_id":null,"runs":[{"run_id":"r","mode":"compare","status":"active","calls_used":1,"max_calls":3,"deadline":100,"source_message_id":12,"room_id":1,"final_result_available":false,"publication":null,"steps":[{"key":"final","member":"2","role":"synthesis","status":"pending","task_id":null,"task_status":null,"updated_at":null,"has_result":false}]}]}"#.utf8)
        let value = try JSONDecoder().decode(CollaborationWorkspacePage.self, from:data)
        XCTAssertEqual(value.runs.first?.steps.first?.taskId,nil)
        XCTAssertEqual(value.runs.first?.finalResultAvailable,false)
        XCTAssertNil(value.nextBeforeSourceId)
    }
    func testOnlyApprovedFinalWithDigestCanPublish() throws {
        let hash = String(repeating:"a",count:64)
        for (step,approved,expected) in [(nil as String?,true,true),("final",true,false),(nil,false,false)] {
            var obj: [String:Any] = ["run_id":"r","approved":approved,"result":"text","sha256":hash]
            if let step { obj["step_key"] = step }
            let value = try JSONDecoder().decode(CollaborationWorkspaceResult.self,from:JSONSerialization.data(withJSONObject:obj))
            XCTAssertEqual(value.canPublishFinal,expected)
        }
    }
    func testUnknownMeteringIsNotZero() throws {
        let data = Data(#"{"run_id":"r","registered_attempts":1,"metered_attempts":0,"unmetered_registered_attempts":1,"reported_input_tokens":null,"reported_output_tokens":null}"#.utf8)
        let value = try JSONDecoder().decode(CollaborationWorkspaceUsage.self,from:data)
        XCTAssertNil(value.reportedInputTokens)
        XCTAssertNil(value.reportedOutputTokens)
    }
}
