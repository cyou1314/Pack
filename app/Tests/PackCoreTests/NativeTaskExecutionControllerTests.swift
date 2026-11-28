import Foundation
import XCTest
@testable import PackCore

final class NativeTaskExecutionControllerTests: XCTestCase {
    private let destination = AgentExecutionDestination(
        accountRef:"12345678-1234-4234-8234-123456789abc",provider:"chatgpt",accessKind:"native")

    private func context() throws -> AgentCollaborationContext {
        let value: [String:Any] = ["run_id":"run","step_key":"answer","provider":"chatgpt",
            "access_kind":"native","account_ref":destination.accountRef,"deadline":Date().timeIntervalSince1970+60,
            "max_bytes":16384,"role":"answer","max_output_tokens":4096,
            "items":[["kind":"owner_message","source_id":1,"sha256":String(repeating:"a",count:64),"text":"fixture"]]]
        return try JSONDecoder().decode(AgentCollaborationContext.self,from:JSONSerialization.data(withJSONObject:value))
    }

    @MainActor private func wait(_ condition: () -> Bool) async throws {
        for _ in 0..<200 {
            if condition() { return }
            try await Task.sleep(for:.milliseconds(10))
        }
        XCTFail("controller state did not arrive")
    }

    @MainActor func testSeparateApprovalTicketsAndReceiptSurvivesUnconfirmedChild() async throws {
        let controller = NativeTaskExecutionController(taskID:1,destination:destination,scopeCurrent:{true})
        let value = try context()
        XCTAssertTrue(controller.start { approve in
            guard try await approve(.context(taskID:1,context:value)) else {
                return .init(receipt:.blocked,childExitConfirmed:true)
            }
            let allowed = try await approve(.send(taskID:1,context:value,origin:"https://chatgpt.com",
                conversationID:"fresh",text:"fixture",sha256:"fixture",deadline:value.deadline))
            return .init(receipt:allowed ? .review : .blocked,childExitConfirmed:false)
        })
        try await wait { controller.pending != nil }
        let first = try XCTUnwrap(controller.pending?.id)
        controller.decide(first,allow:true)
        try await wait { controller.pending != nil && controller.pending?.id != first }
        let second = try XCTUnwrap(controller.pending?.id)
        controller.decide(first,allow:true)
        XCTAssertEqual(controller.pending?.id,second)
        controller.decide(second,allow:true)
        try await wait { controller.phase == .finished }
        XCTAssertEqual(controller.report,.init(receipt:.review,childExitConfirmed:false))
        XCTAssertFalse(controller.start { _ in .init(receipt:nil,childExitConfirmed:false) })
    }

    @MainActor func testTimeoutDeniesWithoutUserAction() async throws {
        let controller = NativeTaskExecutionController(taskID:1,destination:destination,
            approvalTimeout:0.05,scopeCurrent:{true})
        let value = try context()
        XCTAssertTrue(controller.start { approve in
            let allowed = try await approve(.context(taskID:1,context:value))
            return .init(receipt:allowed ? .review : .blocked,childExitConfirmed:true)
        })
        try await wait { controller.phase == .finished }
        XCTAssertNil(controller.pending)
        XCTAssertEqual(controller.report?.receipt,.blocked)
    }

    @MainActor func testStopDeniesPendingAndNeverClaimsRemoteCancellation() async throws {
        let controller = NativeTaskExecutionController(taskID:1,destination:destination,scopeCurrent:{true})
        let value = try context()
        XCTAssertTrue(controller.start { approve in
            let allowed = try await approve(.context(taskID:1,context:value))
            return .init(receipt:allowed ? .review : .stopUnknown,childExitConfirmed:false)
        })
        try await wait { controller.pending != nil }
        controller.stop()
        try await wait { controller.phase == .finished }
        XCTAssertNil(controller.pending)
        XCTAssertEqual(controller.report?.receipt,.stopUnknown)
        XCTAssertEqual(controller.report?.childExitConfirmed,false)
    }

    @MainActor func testForeignTaskAndScopeDriftFailClosed() async throws {
        var current = true
        let controller = NativeTaskExecutionController(taskID:1,destination:destination,scopeCurrent:{current})
        let value = try context()
        XCTAssertTrue(controller.start { approve in
            let foreign = try await approve(.context(taskID:2,context:value))
            guard !foreign else { return .init(receipt:.review,childExitConfirmed:true) }
            let allowed = try await approve(.context(taskID:1,context:value))
            return .init(receipt:allowed ? .review : .blocked,childExitConfirmed:true)
        })
        try await wait { controller.pending != nil }
        let ticket = try XCTUnwrap(controller.pending?.id)
        current = false
        controller.decide(ticket,allow:true)
        try await wait { controller.phase == .finished }
        XCTAssertEqual(controller.report?.receipt,.blocked)
    }
}
