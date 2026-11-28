import Foundation
import XCTest
@testable import PackCore

final class NativeLoginProcessSupervisorTests: XCTestCase {
    @MainActor func testEOFStopsOnlyManagedChildAndRejectsConcurrentLaunch() async throws {
        let supervisor = NativeLoginProcessSupervisor(), id = UUID()
        let executable = URL(fileURLWithPath:"/bin/cat")
        let directory = FileManager.default.temporaryDirectory
        try supervisor.launch(accountID:id,executable:executable,arguments:[],directory:directory,environment:["PATH":"/usr/bin:/bin"])
        XCTAssertEqual(supervisor.phase,.running)
        XCTAssertEqual(supervisor.accountID,id)
        XCTAssertThrowsError(try supervisor.launch(accountID:UUID(),executable:executable,arguments:[],directory:directory,environment:[:]))
        supervisor.stop()
        for _ in 0..<100 where supervisor.phase.active { try await Task.sleep(for:.milliseconds(30)) }
        XCTAssertEqual(supervisor.phase,.closed)
    }

    @MainActor func testNonzeroExitNeverReportsCleanClose() async throws {
        let supervisor = NativeLoginProcessSupervisor()
        try supervisor.launch(accountID:UUID(),executable:URL(fileURLWithPath:"/usr/bin/false"),
            arguments:[],directory:FileManager.default.temporaryDirectory,environment:[:])
        for _ in 0..<100 where supervisor.phase.active { try await Task.sleep(for:.milliseconds(30)) }
        XCTAssertEqual(supervisor.phase,.recoveryRequired)
    }
}
