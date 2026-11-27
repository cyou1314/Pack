import Foundation
import XCTest
@testable import PackCore

private actor BrokerFixtureHub: AgentCollaborationHub {
    var calls: [String] = []
    var failTerminal = false
    func failNextTerminal() { failTerminal = true }
    func callOnce(name: String, arguments: Data) async throws -> Data {
        calls.append(name)
        let result: [String:Any]
        switch name {
        case "task_claim":
            result = ["task_id":1,"status":"claimed","claim_token":"fixture","lease_expires":9999999999,
                      "private_field":"must not escape"]
        case "collaboration_context_get":
            result = ["run_id":"run","step_key":"answer","provider":"chatgpt","access_kind":"native",
                      "account_ref":"12345678-1234-4234-8234-123456789abc","deadline":9999999999,
                      "max_bytes":16384,"role":"answer","max_output_tokens":4096,
                      "items":[["kind":"owner_message","source_id":1,"sha256":String(repeating:"a",count:64),"text":"fixture"]],
                      "private_field":"must not escape"]
        case "run_start": result = ["task_id":1,"status":"running","fence":1]
        case "task_complete","task_block":
            if failTerminal { throw NativeHubBrokerError.unconfirmed }
            result = ["task_id":1,"status":name == "task_complete" ? "review" : "cancelled"]
        default: throw NativeHubBrokerError.invalidRequest
        }
        return try JSONSerialization.data(withJSONObject:result)
    }
}

final class NativeHubTaskBrokerTests: XCTestCase {
    private func fixture() throws -> (NativeHubTaskBroker,BrokerFixtureHub) {
        let hub = BrokerFixtureHub()
        let broker = try NativeHubTaskBroker(hub:hub,taskID:1,destination:.init(
            accountRef:"12345678-1234-4234-8234-123456789abc",provider:"chatgpt",accessKind:"native"))
        return (broker,hub)
    }
    private func call(_ broker: NativeHubTaskBroker,_ seq: Int,_ tool: String,
                      _ args: [String:Any]) async throws -> Data {
        try await broker.exchange(JSONSerialization.data(withJSONObject:[
            "version":1,"sequence":seq,"tool":tool,"arguments":args]))
    }
    func testTaskScopedLifecycleAndNoPrivateResponseFields() async throws {
        let (broker,hub) = try fixture()
        let claim = try await call(broker,1,"task_claim",["task_id":1])
        XCTAssertFalse(String(decoding:claim,as:UTF8.self).contains("private_field"))
        let args: [String:Any] = ["task_id":1,"claim_token":"fixture"]
        let context = try await call(broker,2,"collaboration_context_get",args)
        XCTAssertFalse(String(decoding:context,as:UTF8.self).contains("private_field"))
        _ = try await call(broker,3,"run_start",args)
        _ = try await call(broker,4,"collaboration_context_get",args)
        var completion = args; completion["result"] = "answer"
        _ = try await call(broker,5,"task_complete",completion)
        do { _ = try await call(broker,6,"task_complete",completion); XCTFail("terminal replay") }
        catch {}
        let calls = await hub.calls
        XCTAssertEqual(calls,["task_claim","collaboration_context_get","run_start","collaboration_context_get","task_complete"])
    }

    func testRealNodeChildPrivatePipeRoundTrip() async throws {
        guard let node = ProcessInfo.processInfo.environment["PACK_TEST_NODE"] else {
            throw XCTSkip("Node executable not explicitly configured for integration test")
        }
        let root = URL(fileURLWithPath:#filePath)
            .deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
        let fixtureFile = root.appendingPathComponent("tests/fixtures/native-hub-pipe-child.mjs")
        let (broker,hub) = try fixture()
        try await NativeTaskProcessChannel.run(executable:URL(fileURLWithPath:node),
            arguments:[fixtureFile.path],directory:root,environment:["PATH":"/usr/bin:/bin"],
            broker:broker,timeoutSeconds:20)
        let calls = await hub.calls
        XCTAssertEqual(calls,["task_claim","collaboration_context_get","run_start","collaboration_context_get","task_complete"])
    }

    func testMalformedChildFrameNeverCallsHub() async throws {
        let (broker,hub) = try fixture()
        do {
            try await NativeTaskProcessChannel.run(executable:URL(fileURLWithPath:"/usr/bin/printf"),
                arguments:["abcd"],directory:FileManager.default.temporaryDirectory,
                environment:[:],broker:broker,timeoutSeconds:5)
            XCTFail("oversized header accepted")
        } catch {}
        let calls = await hub.calls
        XCTAssertTrue(calls.isEmpty)
    }

    func testForeignTaskUnknownToolAndWrongClaimNeverReachHub() async throws {
        let (broker,hub) = try fixture()
        for (tool,args) in [
            ("task_claim",["task_id":2] as [String:Any]),
            ("room_post",["task_id":1]),
            ("task_claim",["task_id":true]),
            ("task_claim",["task_id":1,"extra":"forbidden"])
        ] {
            do { _ = try await call(broker,1,tool,args); XCTFail("unexpected request") }
            catch {}
        }
        _ = try await call(broker,1,"task_claim",["task_id":1])
        do {
            _ = try await call(broker,2,"collaboration_context_get",["task_id":1,"claim_token":"wrong"])
            XCTFail("wrong token")
        } catch {}
        let calls = await hub.calls
        XCTAssertEqual(calls,["task_claim"])
    }

    func testUnknownTerminalNeverRetriesOrFallsBackToBlock() async throws {
        let (broker,hub) = try fixture()
        _ = try await call(broker,1,"task_claim",["task_id":1])
        let args: [String:Any] = ["task_id":1,"claim_token":"fixture"]
        _ = try await call(broker,2,"collaboration_context_get",args)
        _ = try await call(broker,3,"run_start",args)
        _ = try await call(broker,4,"collaboration_context_get",args)
        await hub.failNextTerminal()
        var completion = args; completion["result"] = "answer"
        do { _ = try await call(broker,5,"task_complete",completion); XCTFail("unknown delivery") }
        catch {}
        var block = args; block["reason"] = "provider_outcome_unknown"; block["fail_class"] = "task"
        do { _ = try await call(broker,6,"task_block",block); XCTFail("terminal fallback") }
        catch {}
        let calls = await hub.calls
        XCTAssertEqual(calls.filter{$0 == "task_complete"}.count,1)
        XCTAssertFalse(calls.contains("task_block"))
    }
}
