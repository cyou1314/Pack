import Foundation
import PackCore

/// Explicit opt-in API execution factory. Never loads dispatcher config or
/// migrates/stores the supplied dedicated member token. Caller must provision
/// the member securely and supply the shared Node/Swift lease directory.
struct DedicatedCollaborationHub: AgentCollaborationHub {
    private let client: HubClient
    init(url: String, memberToken: String) throws {
        guard PackHubURLPolicy.isAllowed(url), memberToken.hasPrefix("pk-mem-"),
              memberToken.count > 32, memberToken.utf8.count <= 512,
              memberToken.unicodeScalars.allSatisfy({ $0.value >= 33 && $0.value <= 126 })
        else { throw HubError.badResponse("invalid executor credential or Hub URL") }
        client = HubClient(cfg: PackConfig(hubUrl: url, token: memberToken, ownerToken: nil))
    }
    func callOnce(name: String, arguments: Data) async throws -> Data {
        try await client.collaborationCallOnce(name: name, arguments: arguments)
    }
}

func makeAPICollaborationHost(hubURL: String, dedicatedMemberToken: String,
                              registry: AgentRegistryStore, leaseRoot: URL,
                              expected: AgentExecutionDestination) throws -> AgentAPICollaborationHost {
    let hub = try DedicatedCollaborationHub(url: hubURL, memberToken: dedicatedMemberToken)
    return AgentAPICollaborationHost(hub: hub, registry: registry, leaseRoot: leaseRoot, expected: expected)
}


/// Revalidate the instance and dedicated identity before each Hub operation.
/// Token is scoped to the stored instance. No automatic mutation retry.
struct InstanceBoundCollaborationHub: AgentCollaborationHub {
    let client: DedicatedCollaborationHub
    let instance: AgentHubInstance
    let destination: AgentExecutionDestination
    let memberId: Int

    func callOnce(name: String, arguments: Data) async throws -> Data {
        try Task.checkCancellation()
        let data = try await client.callOnce(name:"collaboration_executor_identity",arguments:Data("{}".utf8))
        let identity = try JSONDecoder().decode(AgentExecutorIdentity.self,from:data)
        guard identity.matches(instance:instance,destination:destination,memberId:memberId)
        else { throw AgentEnrollmentError.receiptMismatch }
        try Task.checkCancellation()
        return try await client.callOnce(name:name,arguments:arguments)
    }
}
