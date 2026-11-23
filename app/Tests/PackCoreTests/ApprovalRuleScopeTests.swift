import XCTest
@testable import PackCore

final class ApprovalRuleScopeTests: XCTestCase {
    func testUsesFirstActionTagAndTrimsAssignedMember() throws {
        let scope = try XCTUnwrap(
            ApprovalRuleScope(
                taskTitle: "生成素材 [ENG-image]，之后再交给 [M4A]",
                assignee: "  absent \n"
            )
        )

        XCTAssertEqual(scope.action, "[ENG-image]")
        XCTAssertEqual(scope.target, "absent")
    }

    func testSupportsTheSameActionTagCharactersAsHub() throws {
        let scope = try XCTUnwrap(
            ApprovalRuleScope(taskTitle: "任务 [A1_B-2]", assignee: "member")
        )

        XCTAssertEqual(scope.action, "[A1_B-2]")
        XCTAssertEqual(scope.target, "member")
    }

    func testRejectsTitleWithoutActionTag() {
        XCTAssertNil(
            ApprovalRuleScope(taskTitle: "普通任务", assignee: "member")
        )
    }

    func testRejectsUnsupportedOrEmptyActionTags() {
        XCTAssertNil(
            ApprovalRuleScope(taskTitle: "任务 [中文]", assignee: "member")
        )
        XCTAssertNil(
            ApprovalRuleScope(taskTitle: "任务 [ENG image]", assignee: "member")
        )
        XCTAssertNil(
            ApprovalRuleScope(taskTitle: "任务 []", assignee: "member")
        )
    }

    func testRejectsMissingOrWhitespaceAssignedMember() {
        let assignees: [String?] = [nil, "", " \n\t "]

        for assignee in assignees {
            XCTAssertNil(
                ApprovalRuleScope(taskTitle: "任务 [ENG]", assignee: assignee)
            )
        }
    }
}
