import XCTest
@testable import PackCore

final class HubEndpointTests: XCTestCase {
    func testRemoteHTTPSIsAllowed() {
        XCTAssertNotNil(HubEndpoint.validatedURL("https://hub.example.com/mcp"))
    }

    func testLoopbackHTTPIsAllowed() {
        XCTAssertNotNil(HubEndpoint.validatedURL("http://127.0.0.1:8794/mcp"))
        XCTAssertNotNil(HubEndpoint.validatedURL("http://localhost:8794/mcp"))
        XCTAssertNotNil(HubEndpoint.validatedURL("http://[::1]:8794/mcp"))
    }

    func testRemoteHTTPIsRejected() {
        XCTAssertNil(HubEndpoint.validatedURL("http://hub.example.com/mcp"))
        XCTAssertNil(HubEndpoint.validatedURL("http://192.168.1.10:8794/mcp"))
    }

    func testNonHTTPProtocolsAreRejected() {
        XCTAssertNil(HubEndpoint.validatedURL("ftp://hub.example.com/file"))
        XCTAssertNil(HubEndpoint.validatedURL("file:///tmp/pack"))
    }

    func testCredentialsQueryAndFragmentAreRejected() {
        XCTAssertNil(HubEndpoint.validatedURL("https://user:pass@hub.example.com/mcp"))
        XCTAssertNil(HubEndpoint.validatedURL("https://hub.example.com/mcp?token=secret"))
        XCTAssertNil(HubEndpoint.validatedURL("https://hub.example.com/mcp#fragment"))
    }

    func testWhitespaceAroundAddressIsRejected() {
        XCTAssertNil(HubEndpoint.validatedURL(" https://hub.example.com/mcp "))
    }
}

    func testRedirectMustRemainOnTheSameSecureOrigin() {
        let origin = URL(string: "https://hub.example.com/mcp")!
        XCTAssertTrue(HubEndpoint.allowsRedirect(
            from: origin,
            to: URL(string: "https://hub.example.com/v1/mcp")!
        ))
        XCTAssertTrue(HubEndpoint.allowsRedirect(
            from: origin,
            to: URL(string: "https://hub.example.com:443/v1/mcp")!
        ))
        XCTAssertFalse(HubEndpoint.allowsRedirect(
            from: origin,
            to: URL(string: "http://hub.example.com/mcp")!
        ))
        XCTAssertFalse(HubEndpoint.allowsRedirect(
            from: origin,
            to: URL(string: "https://other.example.com/mcp")!
        ))
    }
