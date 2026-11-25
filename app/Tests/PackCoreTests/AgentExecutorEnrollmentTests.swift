import XCTest
import CryptoKit
@testable import PackCore

private actor EnrollmentBackend: AgentSecretBackend {
    private var records:[String:Data] = [:]
    func insert(account:String,secret:Data) async throws {
        guard records[account] == nil else { throw AgentVaultError.duplicate }
        records[account] = secret
    }
    func read(account:String) async throws -> Data {
        guard let data = records[account] else { throw AgentVaultError.missing }
        return data
    }
    func replace(account:String,secret:Data) async throws { throw AgentVaultError.invalidReference }
    func remove(account:String) async throws { throw AgentVaultError.invalidReference }
}
private actor EnrollmentFixture {
    private var requests:[AgentExecutorProvisionRequest] = []
    private var lose = true
    func provision(_ request:AgentExecutorProvisionRequest) throws -> AgentExecutorProvisionReceipt {
        requests.append(request)
        if lose { lose = false; throw AgentEnrollmentError.receiptMismatch }
        return .init(instanceId:request.instanceId,accountRef:request.accountRef,
            provider:request.provider,accessKind:request.accessKind,memberId:42,created:false)
    }
    func verify(_ token:String,_ instance:AgentHubInstance,_ dest:AgentExecutionDestination) -> Bool {
        guard let r = requests.last else { return false }
        return r.tokenSHA256 == SHA256.hash(data:Data(token.utf8)).map { String(format:"%02x",$0) }.joined()
            && r.instanceId == instance.instanceId && r.accountRef == dest.accountRef
    }
    func history() -> [AgentExecutorProvisionRequest] { requests }
}
final class AgentExecutorEnrollmentTests: XCTestCase {
    func testUnknownProvisionReusesPersistedSecretAndDifferentDatabaseIsolated() async throws {
        let backend = EnrollmentBackend(), fixture = EnrollmentFixture()
        let instance = AgentHubInstance(instanceId:String(repeating:"a",count:32))
        let other = AgentHubInstance(instanceId:String(repeating:"b",count:32))
        let dest = AgentExecutionDestination(accountRef:UUID().uuidString.lowercased(),provider:"gemini-api",accessKind:"api")
        let first = AgentExecutorEnrollment(backend:backend)
        do {
            _ = try await first.enroll(instance:instance,destination:dest,
                provision:{ try await fixture.provision($0) },verify:{ await fixture.verify($0,$1,$2) })
            XCTFail("expected unknown receipt")
        } catch { XCTAssertEqual(error as? AgentEnrollmentError,.receiptMismatch) }
        let replacement = AgentExecutorEnrollment(backend:backend)
        let receipt = try await replacement.enroll(instance:instance,destination:dest,
            provision:{ try await fixture.provision($0) },verify:{ await fixture.verify($0,$1,$2) })
        XCTAssertEqual(receipt.memberId,42)
        _ = try await replacement.enroll(instance:other,destination:dest,
            provision:{ try await fixture.provision($0) },verify:{ await fixture.verify($0,$1,$2) })
        let requests = await fixture.history()
        XCTAssertEqual(requests.count,3)
        XCTAssertEqual(requests[0],requests[1])
        XCTAssertNotEqual(requests[0].tokenSHA256,requests[2].tokenSHA256)
    }
}
