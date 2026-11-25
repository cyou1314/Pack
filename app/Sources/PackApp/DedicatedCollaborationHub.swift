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
