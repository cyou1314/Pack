import XCTest
@testable import PackCore

final class CollaborationCreateModelsTests: XCTestCase {
    private func preview(duplicate: Bool = false, quota: String = "available") throws -> CollaborationRoutePreview {
        let candidates: [[String:Any]] = [1,2].map {
            ["member_id":$0,"member_name":"Agent \($0)","provider":"openai","access_kind":"api",
             "online":true,"queue":0,"active_plan":false,"quota_state":quota]
        }
        let obj: [String:Any] = ["mode":"compare","requires_owner_confirmation":true,
          "candidates":candidates,"suggestion":["leader":1,"workers":[duplicate ? 1 : 2],"planned_calls":3]]
        return try JSONDecoder().decode(CollaborationRoutePreview.self,from:JSONSerialization.data(withJSONObject:obj))
    }
    func testFrozenPayloadAndBudget() throws {
        var options = CollaborationRouteOptions()
        options.mode = .compare; options.maxCalls = 3
        let route = try preview()
        XCTAssertTrue(route.accepts(options))
        let id = UUID(uuidString:"12345678-1234-4234-8234-123456789abc")!
        let request = try XCTUnwrap(CollaborationCreateRequest(room:"pack-main",body:"test",options:options,
            preview:route,durationMinutes:15,now:Date(timeIntervalSince1970:100),id:id))
        XCTAssertEqual(request.requestKey,"12345678123442348234123456789abc")
        XCTAssertEqual(request.deadline,1000)
        options.maxCalls = 2
        XCTAssertFalse(route.accepts(options))
        XCTAssertEqual(request.options.maxCalls,3)
    }
    func testRejectDuplicateAndUnknownQuotaWithoutConsent() throws {
        var options = CollaborationRouteOptions()
        options.mode = .compare
        XCTAssertFalse(try preview(duplicate:true).accepts(options))
        let unknown = try preview(quota:"unknown")
        XCTAssertFalse(unknown.accepts(options))
        options.allowUnknownQuota = true
        XCTAssertTrue(unknown.accepts(options))
    }
}
