import Foundation
import XCTest
@testable import PackCore

private actor APISecretFixture: AgentSecretBackend {
    var values: [String: Data] = [:]
    func insert(account: String, secret: Data) async throws { values[account] = secret }
    func read(account: String) async throws -> Data {
        guard let data = values[account] else { throw AgentVaultError.missing }
        return data
    }
    func replace(account: String, secret: Data) async throws { values[account] = secret }
    func remove(account: String) async throws { values.removeValue(forKey: account) }
}
private actor HTTPFixture: AgentHTTPTransport {
    let result: AgentHTTPResult
    var requests: [URLRequest] = []
    init(status: Int = 200, json: String) {
        result = .init(status: status, body: Data(json.utf8))
    }
    func send(_ request: URLRequest) async throws -> AgentHTTPResult {
        requests.append(request)
        return result
    }
}

final class AgentOfficialAPIAdapterTests: XCTestCase {
    private func fixture(_ provider: String, http: HTTPFixture) async throws -> (AgentOfficialAPIAdapter, AgentExecutionDestination) {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false,
            attributes: [.posixPermissions: 0o700])
        addTeardownBlock { try FileManager.default.removeItem(at: directory) }
        let registry = try AgentRegistryStore(directory: directory)
        let vault = AgentCredentialVault(backend: APISecretFixture())
        let id = UUID()
        let profile = AgentProfile(id: id, providerID: provider, displayName: provider, mode: .officialAPI,
            modelID: "fixture-model", credentialReference: .forProfile(id: id, mode: .officialAPI),
            contextBoundary: .packWorkspace, memoryBoundary: .disabled)
        try await registry.insert(profile)
        try await vault.insert("fixture-secret", for: profile)
        return (.init(registry: registry, vault: vault, transport: http),
                .init(accountRef: id.uuidString.lowercased(), provider: provider, accessKind: "api"))
    }
    private var items: [AgentAPIContextItem] {
        [.init(kind: "owner_message", sourceID: 1, sha256: String(repeating: "a", count: 64), text: "approved task")]
    }

    func testThreeProviderShapesAndUsage() async throws {
        let fixtures: [(String,String,String,Int)] = [
            ("openai","api.openai.com", #"{"status":"completed","output":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":"answer"}]}],"usage":{"input_tokens":5,"output_tokens":3}}"#,3),
            ("gemini-api","generativelanguage.googleapis.com", #"{"candidates":[{"finishReason":"STOP","content":{"parts":[{"text":"hidden","thought":true},{"text":"answer"}]}}],"usageMetadata":{"promptTokenCount":5,"totalTokenCount":9,"candidatesTokenCount":3,"thoughtsTokenCount":1}}"#,4),
            ("volcengine","ark.cn-beijing.volces.com", #"{"choices":[{"finish_reason":"stop","message":{"content":"answer"}}],"usage":{"prompt_tokens":5,"completion_tokens":3}}"#,3)
        ]
        for (provider,host,json,output) in fixtures {
            let http = HTTPFixture(json: json)
            let (adapter,destination) = try await fixture(provider,http: http)
            let result = try await adapter.execute(destination: destination,items: items,maxOutputTokens: 100,
                deadline: Date().addingTimeInterval(60),authorize: { approval in
                    approval.url.host == host && approval.bodySHA256.count == 64 && approval.destination == destination
                })
            XCTAssertEqual(result,.succeeded(text: "answer",usage: .init(inputTokens: 5,outputTokens: output)))
            let requests = await http.requests
            XCTAssertEqual(requests.count,1)
            let request = try XCTUnwrap(requests.first)
            XCTAssertEqual(request.url?.host,host)
            XCTAssertNil(request.url?.query)
            XCTAssertEqual(request.value(forHTTPHeaderField: provider == "gemini-api" ? "x-goog-api-key" : "Authorization"),
                           provider == "gemini-api" ? "fixture-secret" : "Bearer fixture-secret")
            let body = try XCTUnwrap(request.httpBody)
            XCTAssertFalse(String(decoding: body, as: UTF8.self).contains("fixture-secret"))
            if provider == "openai" {
                let json = try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String:Any])
                XCTAssertEqual(json["store"] as? Bool,false)
            }
        }
    }

    func testDeniedAuthorizationNeverSendsAndQuotaDoesNotRetry() async throws {
        let http = HTTPFixture(status: 429,json: #"{"error":"fixture-secret"}"#)
        let (adapter,destination) = try await fixture("openai",http: http)
        do {
            _ = try await adapter.execute(destination: destination,items: items,maxOutputTokens: 100,
                deadline: Date().addingTimeInterval(60),authorize: { _ in false })
            XCTFail("authorization denial ignored")
        } catch { XCTAssertEqual(error as? AgentAPIError,.unauthorized) }
        let denied = await http.requests
        XCTAssertEqual(denied.count,0)
        let outcome = try await adapter.execute(destination: destination,items: items,maxOutputTokens: 100,
            deadline: Date().addingTimeInterval(60),authorize: { _ in true })
        XCTAssertEqual(outcome,.failed(code:"quota",usage:nil))
        let requests = await http.requests
        XCTAssertEqual(requests.count,1)
    }
    func testIncompleteResponseIsNotSuccessful() async throws {
        let http = HTTPFixture(json: #"{"status":"incomplete","output":[],"usage":{"input_tokens":4,"output_tokens":1}}"#)
        let (adapter,destination) = try await fixture("openai",http: http)
        let outcome = try await adapter.execute(destination: destination,items: items,maxOutputTokens: 100,
            deadline: Date().addingTimeInterval(60),authorize: { _ in true })
        XCTAssertEqual(outcome,.failed(code:"provider_rejected",usage:.init(inputTokens:4,outputTokens:1)))
    }
}
