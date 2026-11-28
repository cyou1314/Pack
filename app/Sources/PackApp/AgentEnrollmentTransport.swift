import Foundation
import PackCore

/// Captured owner client + URL, not a dynamically switched AppState client.
/// The instance is rechecked by Hub on each mutation.
struct AgentEnrollmentTransport: Sendable {
    let owner: HubClient
    let url: String

    private func request<T: Decodable>(_ name:String,_ args:[String:Any],as type:T.Type) async throws -> T {
        let data = try await owner.ownerWorkspaceCall(name:name,args:args)
        return try JSONDecoder().decode(type,from:data)
    }

    func instance() async throws -> AgentHubInstance {
        let value = try await request("collaboration_instance_info",[:],as:AgentHubInstance.self)
        guard value.isValid else { throw AgentEnrollmentError.invalidIdentity }
        return value
    }

    func status(_ instance:AgentHubInstance,_ destination:AgentExecutionDestination) async throws -> AgentExecutorBindingStatus {
        let value = try await request("collaboration_executor_status",[
            "instance_id":instance.instanceId,"account_ref":destination.accountRef
        ],as:AgentExecutorBindingStatus.self)
        guard value.matches(instance:instance,destination:destination) else { throw AgentEnrollmentError.receiptMismatch }
        return value
    }

    func provision(_ value:AgentExecutorProvisionRequest) async throws -> AgentExecutorProvisionReceipt {
        try await request("collaboration_executor_provision",[
            "instance_id":value.instanceId,"account_ref":value.accountRef,"provider":value.provider,
            "access_kind":value.accessKind,"token_sha256":value.tokenSHA256
        ],as:AgentExecutorProvisionReceipt.self)
    }

    func verify(_ token:String,_ instance:AgentHubInstance,_ destination:AgentExecutionDestination,_ memberId:Int) async throws -> Bool {
        let client = try DedicatedCollaborationHub(url:url,memberToken:token)
        let bytes = try await client.callOnce(name:"collaboration_executor_identity",arguments:Data("{}".utf8))
        let identity = try JSONDecoder().decode(AgentExecutorIdentity.self,from:bytes)
        return identity.matches(instance:instance,destination:destination,memberId:memberId)
    }

    func revoke(_ instance:AgentHubInstance,_ destination:AgentExecutionDestination,_ memberId:Int) async throws {
        let receipt = try await request("collaboration_executor_revoke",[
            "instance_id":instance.instanceId,"account_ref":destination.accountRef,"member_id":memberId
        ],as:AgentExecutorRevokeReceipt.self)
        guard receipt.revoked, receipt.memberId == memberId,
              receipt.instanceId == instance.instanceId,receipt.accountRef == destination.accountRef else {
            throw AgentEnrollmentError.receiptMismatch
        }
    }
}
