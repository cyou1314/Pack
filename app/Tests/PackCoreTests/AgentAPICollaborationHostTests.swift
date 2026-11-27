import Foundation
import XCTest
@testable import PackCore

private actor HostVault: AgentSecretBackend {
    var values: [String: Data] = [:]
    func insert(account: String, secret: Data) async throws { values[account] = secret }
    func read(account: String) async throws -> Data {
        guard let value = values[account] else { throw AgentVaultError.missing }
        return value
    }
    func replace(account: String, secret: Data) async throws { values[account] = secret }
    func remove(account: String) async throws { values.removeValue(forKey: account) }
}
private actor HostHTTP: AgentHTTPTransport {
    var count = 0
    func send(_ request: URLRequest) async throws -> AgentHTTPResult {
        count += 1
        return .init(status: 200, body: Data(#"{"status":"completed","output":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":"answer"}]}],"usage":{"input_tokens":4,"output_tokens":2}}"#.utf8))
    }
}
private actor HostHub: AgentCollaborationHub {
    let destination: AgentExecutionDestination
    let deadline: Double
    let changed: Bool
    let deliveryUnknown: Bool
    var calls: [String] = []
    var reads = 0
    var refuseClaim = false
    func refuseNextClaim() { refuseClaim = true }
    init(destination: AgentExecutionDestination, changed: Bool, deliveryUnknown: Bool) {
        self.destination = destination
        self.changed = changed
        self.deliveryUnknown = deliveryUnknown
        self.deadline = Date().addingTimeInterval(120).timeIntervalSince1970
    }
    func callOnce(name: String, arguments: Data) async throws -> Data {
        calls.append(name)
        let args = try JSONSerialization.jsonObject(with: arguments) as? [String: Any] ?? [:]
        let id = args["task_id"] as? Int ?? 1
        let result: [String: Any]
        switch name {
        case "collaboration_executor_info": return try JSONEncoder().encode(destination)
        case "task_claim":
            if refuseClaim {
                refuseClaim = false
                result = ["task_id": id, "status":"unavailable","code":"execution_capacity"]
            } else {
                result = ["task_id": id, "status":"claimed","claim_token":"fixture","lease_expires":deadline]
            }
        case "collaboration_context_get":
            reads += 1
            result = ["run_id":"run","step_key":"answer","provider":destination.provider,
                      "account_ref":destination.accountRef,"access_kind":"api","deadline":deadline,"max_bytes":16384,"role":"answer","max_output_tokens":4096,
                      "items":[["kind":"owner_message","source_id":1,"sha256":String(repeating:"a",count:64),
                                "text":changed && reads > 1 ? "changed" : "approved"]]]
        case "run_start": result = ["task_id":id,"status":"running","fence":1]
        case "task_complete":
            if deliveryUnknown { throw AgentAPIError.ambiguousTransport }
            guard args["result"] as? String == "answer",
                  let usage = args["provider_usage"] as? [String: Int],
                  usage == ["input_tokens":4,"output_tokens":2] else { throw AgentAPIError.invalidRequest }
            result = ["task_id":id,"status":"review"]
        case "task_block": result = ["task_id":id,"status":"cancelled"]
        default: throw AgentAPIError.invalidRequest
        }
        return try JSONSerialization.data(withJSONObject:result)
    }
}
final class AgentAPICollaborationHostTests: XCTestCase {
    private func fixture(changed: Bool = false, deliveryUnknown: Bool = false,
                         admission: AgentCollaborationAdmission = .init()) async throws
        -> (AgentAPICollaborationHost, HostHub, HostHTTP, URL, String) {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false, attributes:[.posixPermissions:0o700])
        addTeardownBlock { try FileManager.default.removeItem(at:root) }
        let registry = try AgentRegistryStore(directory:root)
        let vault = AgentCredentialVault(backend:HostVault())
        let id = UUID()
        let profile = AgentProfile(id:id, providerID:"openai",displayName:"fixture",mode:.officialAPI,
            modelID:"fixture-model",credentialReference:.forProfile(id:id,mode:.officialAPI),
            contextBoundary:.packWorkspace,memoryBoundary:.disabled)
        try await registry.insert(profile)
        try await vault.insert("fixture-key",for:profile)
        let ref = id.uuidString.lowercased()
        let dest = AgentExecutionDestination(accountRef:ref,provider:"openai",accessKind:"api")
        let hub = HostHub(destination:dest,changed:changed,deliveryUnknown:deliveryUnknown)
        let http = HostHTTP()
        let host = AgentAPICollaborationHost(hub:hub,registry:registry,vault:vault,transport:http,leaseRoot:root,expected:dest,admission:admission)
        return (host,hub,http,root,ref)
    }

    func testCompletePathUsesExistingAdapterAndReleasesLease() async throws {
        let (host,hub,http,root,ref) = try await fixture()
        let result = await host.execute(taskID:1,maxOutputTokens:100,authorize:{ approval in
            approval.bodySHA256.count == 64 && String(data:approval.body,encoding:.utf8)?.contains("approved") == true
                && String(data:approval.body,encoding:.utf8)?.contains("fixture-key") == false
        })
        XCTAssertEqual(result,.review(usage:.init(inputTokens:4,outputTokens:2)))
        let calls = await hub.calls
        let count = await http.count
        XCTAssertEqual(calls,["collaboration_executor_info","task_claim","collaboration_context_get","run_start",
                              "collaboration_context_get","task_complete"])
        XCTAssertEqual(count,1)
        let lease = try AgentSharedAccountLease(root:root,accountRef:ref)
        try lease.release()
    }

    func testChangedContextNeverSends() async throws {
        let (host,hub,http,_,_) = try await fixture(changed:true)
        let result = await host.execute(taskID:1,maxOutputTokens:100,authorize:{ _ in true })
        XCTAssertEqual(result,.blocked(code:"provider_rejected",usage:nil))
        let count = await http.count
        let calls = await hub.calls
        XCTAssertEqual(count,0)
        XCTAssertEqual(calls.last,"task_block")
    }

    func testDefinitiveCapacityRefusalAllowsLaterExplicitAttempt() async throws {
        let (host,hub,http,_,_) = try await fixture()
        await hub.refuseNextClaim()
        let refused = await host.execute(taskID:1,maxOutputTokens:100,authorize:{ _ in true })
        XCTAssertEqual(refused,.unavailable)
        let before = await host.executionStatus()
        XCTAssertEqual(before.tracked,0)
        let countBefore = await http.count
        XCTAssertEqual(countBefore,0)
        let result = await host.execute(taskID:1,maxOutputTokens:100,authorize:{ _ in true })
        XCTAssertEqual(result,.review(usage:.init(inputTokens:4,outputTokens:2)))
        let countAfter = await http.count
        XCTAssertEqual(countAfter,1)
    }

    func testSharedAdmissionAcrossHostsRejectsDuplicateBeforeClaim() async throws {
        let admission = AgentCollaborationAdmission()
        let (first,_,http,_,_) = try await fixture(admission:admission)
        let (second,hub,otherHTTP,_,_) = try await fixture(admission:admission)
        let result = await first.execute(taskID:1,maxOutputTokens:100,authorize:{ _ in true })
        XCTAssertEqual(result,.review(usage:.init(inputTokens:4,outputTokens:2)))
        let duplicate = await second.execute(taskID:1,maxOutputTokens:100,authorize:{ _ in true })
        XCTAssertEqual(duplicate,.unavailable)
        let calls = await hub.calls
        let count = await http.count
        let other = await otherHTTP.count
        XCTAssertTrue(calls.isEmpty)
        XCTAssertEqual(count,1)
        XCTAssertEqual(other,0)
        let status = await first.executionStatus()
        XCTAssertEqual(status.outcomes,["review":1])
    }

    func testAdmissionCapacityUnknownAndStop() async {
        let admission = AgentCollaborationAdmission(maxConcurrent:1,maxTracked:2)
        let first = await admission.begin(1)
        let duplicate = await admission.begin(1)
        let capacity = await admission.begin(2)
        XCTAssertTrue(first)
        XCTAssertFalse(duplicate)
        XCTAssertFalse(capacity)
        await admission.finish(1,result:.claimUnknown)
        let retry = await admission.begin(1)
        let second = await admission.begin(2)
        XCTAssertFalse(retry)
        XCTAssertTrue(second)
        await admission.finish(2,result:.unavailable)
        let safeRetry = await admission.begin(2)
        XCTAssertTrue(safeRetry)
        await admission.finish(2,result:.deliveryUnknown(usage:nil))
        let full = await admission.begin(3)
        XCTAssertFalse(full)
        await admission.stopAdmission()
        let status = await admission.snapshot()
        XCTAssertFalse(status.accepting)
        XCTAssertEqual(status.active,0)
        XCTAssertEqual(status.outcomes,["claimUnknown":1,"deliveryUnknown":1])
    }

    func testUnknownDeliveryRetainsCrossProcessMarker() async throws {
        let (host,hub,http,root,ref) = try await fixture(deliveryUnknown:true)
        let result = await host.execute(taskID:1,maxOutputTokens:100,authorize:{ _ in true })
        XCTAssertEqual(result,.deliveryUnknown(usage:.init(inputTokens:4,outputTokens:2)))
        XCTAssertThrowsError(try AgentSharedAccountLease(root:root,accountRef:ref))
        let count = await http.count
        let calls = await hub.calls
        XCTAssertEqual(count,1)
        XCTAssertEqual(calls.filter{$0 == "task_complete"}.count,1)
        XCTAssertFalse(calls.contains("task_block"))
        let duplicate = await host.execute(taskID:1,maxOutputTokens:100,authorize:{ _ in true })
        XCTAssertEqual(duplicate,.unavailable)
        let after = await hub.calls
        XCTAssertEqual(after,calls)
    }
}
