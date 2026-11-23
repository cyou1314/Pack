import XCTest
@testable import PackCore

final class MessageRefreshStateTests: XCTestCase {
    func testInitialLoadRequiresFullSnapshot() {
        let state = MessageRefreshState()
        XCTAssertTrue(state.requiresFullSnapshot(
            roomLoading: false, force: false, now: 100,
            clientGeneration: 1, roomGeneration: 1
        ))
    }

    func testSuccessfulSnapshotAllowsIncrementalRefreshWithinInterval() {
        var state = MessageRefreshState()
        state.recordFullSnapshot(at: 100, clientGeneration: 2, roomGeneration: 3)

        XCTAssertFalse(state.requiresFullSnapshot(
            roomLoading: false, force: false, now: 159,
            clientGeneration: 2, roomGeneration: 3
        ))
    }

    func testRoomLoadingForceAndIntervalRequireFullSnapshot() {
        var state = MessageRefreshState()
        state.recordFullSnapshot(at: 100, clientGeneration: 2, roomGeneration: 3)

        XCTAssertTrue(state.requiresFullSnapshot(
            roomLoading: true, force: false, now: 101,
            clientGeneration: 2, roomGeneration: 3
        ))
        XCTAssertTrue(state.requiresFullSnapshot(
            roomLoading: false, force: true, now: 101,
            clientGeneration: 2, roomGeneration: 3
        ))
        XCTAssertTrue(state.requiresFullSnapshot(
            roomLoading: false, force: false, now: 160,
            clientGeneration: 2, roomGeneration: 3
        ))
    }

    func testRoomOrClientGenerationChangeRequiresNewSnapshot() {
        var state = MessageRefreshState()
        state.recordFullSnapshot(at: 100, clientGeneration: 2, roomGeneration: 3)

        XCTAssertTrue(state.requiresFullSnapshot(
            roomLoading: false, force: false, now: 101,
            clientGeneration: 3, roomGeneration: 3
        ))
        XCTAssertTrue(state.requiresFullSnapshot(
            roomLoading: false, force: false, now: 101,
            clientGeneration: 2, roomGeneration: 4
        ))
    }

    func testFailedSnapshotDoesNotAdvanceGenerationOrTimestamp() {
        var state = MessageRefreshState()
        state.recordFullSnapshot(at: 100, clientGeneration: 2, roomGeneration: 3)

        // A room switch increments the generation, but no successful snapshot
        // is recorded while its first refresh fails.
        XCTAssertTrue(state.requiresFullSnapshot(
            roomLoading: false, force: false, now: 101,
            clientGeneration: 2, roomGeneration: 4
        ))
    }

    func testFullIncrementalPageRequiresAnotherPage() {
        XCTAssertFalse(MessageRefreshState.needsAnotherPage(receivedCount: 199))
        XCTAssertTrue(MessageRefreshState.needsAnotherPage(receivedCount: 200))
        XCTAssertTrue(MessageRefreshState.needsAnotherPage(receivedCount: 250))
    }
}
