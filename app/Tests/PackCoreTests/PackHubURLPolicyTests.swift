import XCTest
@testable import PackCore

final class PackHubURLPolicyTests: XCTestCase {
    func testAllowsLocalPackHub() {
        XCTAssertTrue(PackHubURLPolicy.isAllowed("http://127.0.0.1:8794/mcp"))
        XCTAssertTrue(PackHubURLPolicy.isAllowed("http://localhost:8794/mcp"))
        XCTAssertTrue(PackHubURLPolicy.isAllowed("http://[::1]:8794/mcp"))
    }

    func testAllowsPackPinggyTunnel() {
        XCTAssertTrue(PackHubURLPolicy.isAllowed(
            "https://uejim-2a04-4e41-8-1eea--a5a-9eea.run.pinggy-free.link/mcp"))
        XCTAssertTrue(PackHubURLPolicy.isAllowed("https://pinggy-free.link/mcp"))
        XCTAssertTrue(PackHubURLPolicy.isAllowed("https://subdomain.pinggy-free.link:443/mcp"))
    }

    func testRejectsUntrustedHostsAndSchemes() {
        XCTAssertFalse(PackHubURLPolicy.isAllowed("https://attacker.example/mcp"))
        XCTAssertFalse(PackHubURLPolicy.isAllowed("http://attacker.example/mcp"))
        XCTAssertFalse(PackHubURLPolicy.isAllowed("ftp://pinggy-free.link/mcp"))
        XCTAssertFalse(PackHubURLPolicy.isAllowed("https://notpinggy-free.link/mcp"))
        XCTAssertFalse(PackHubURLPolicy.isAllowed("https://pinggy-free.link.evil.example/mcp"))
    }

    func testRejectsCredentialAndURLInjection() {
        XCTAssertFalse(PackHubURLPolicy.isAllowed("https://user:pass@sub.pinggy-free.link/mcp"))
        XCTAssertFalse(PackHubURLPolicy.isAllowed("https://sub.pinggy-free.link/mcp?token=leak"))
        XCTAssertFalse(PackHubURLPolicy.isAllowed("https://sub.pinggy-free.link/mcp#fragment"))
        XCTAssertFalse(PackHubURLPolicy.isAllowed("https://sub.pinggy-free.link/other"))
        XCTAssertFalse(PackHubURLPolicy.isAllowed("https://sub.pinggy-free.link:8443/mcp"))
    }

    func testLocalHubMustUseFixedPort() {
        XCTAssertFalse(PackHubURLPolicy.isAllowed("http://127.0.0.1:8080/mcp"))
        XCTAssertFalse(PackHubURLPolicy.isAllowed("http://127.0.0.1/mcp"))
    }
}
