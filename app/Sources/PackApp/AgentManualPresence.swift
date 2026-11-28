import Foundation
import PackCore

struct ManualPresenceSnapshot {
    let label: String
    let running: Bool
    let tasks: [ManualTask]
    let revision = UUID()
}
private struct PresenceReceipt: Decodable, Sendable {
    let instanceId: String
    let memberId: Int
    let sessionId: String
    let accepting: Bool
    let expiresAt: Double
    enum CodingKeys: String,CodingKey {
        case instanceId = "instance_id", memberId = "member_id", sessionId = "session_id",
             accepting, expiresAt = "expires_at"
    }
}

extension AppState {
    func startManualPresence(account: AgentAccountSummary, scope: String,
                             instance: AgentHubInstance, memberId: Int) throws {
        guard account.profile.mode == .officialAPI, account.credentialState == .apiKeyPresentUnverified,
              manualPresenceJobs[account.id] == nil, collaborationOwnerAvailable
        else { throw AgentEnrollmentError.invalidIdentity }
        let transport = try executorEnrollmentTransport(scope:scope)
        let generation = clientGeneration, id = account.id
        let destination = AgentExecutionDestination(accountRef:id.uuidString.lowercased(),
            provider:account.profile.providerID,accessKind:"api")
        let session = UUID().uuidString.replacingOccurrences(of:"-",with:"").lowercased()
        manualPresence[id] = .init(label:"接单监听启动中",running:true,tasks:[])
        manualPresenceJobs[id] = Task { [weak self] in
            guard let self else { return }
            var started = false
            var stopConfirmed = false
            var failed = false
            do {
                guard try await transport.instance() == instance else { throw AgentEnrollmentError.invalidIdentity }
                let status = try await transport.status(instance,destination)
                guard status.state == "bound",status.memberId == memberId else { throw AgentEnrollmentError.invalidIdentity }
                while !Task.isCancelled, self.clientGeneration == generation, self.collaborationOwnerAvailable {
                    let action = started ? "collaboration_presence_renew" : "collaboration_presence_start"
                    let rows = try await Self.presenceTick(transport:transport,instance:instance,
                        destination:destination,memberId:memberId,session:session,action:action)
                    started = true
                    guard !Task.isCancelled,self.clientGeneration == generation,self.collaborationOwnerAvailable else { break }
                    self.manualPresence[id] = .init(label:"接单监听中 · 逐次人工授权",running:true,tasks:rows)
                    try await Task.sleep(for:.seconds(20))
                }
            } catch {
                failed = !Task.isCancelled
            }
            // A cancellation must not prevent best-effort stop. The stop uses
            // the captured old Hub and exact session; never a replacement.
            let stop = Task.detached {
                do {
                    _ = try await Self.presenceTick(transport:transport,instance:instance,
                        destination:destination,memberId:memberId,session:session,
                        action:"collaboration_presence_stop")
                    return true
                } catch { return false }
            }
            stopConfirmed = await stop.value
            self.manualPresenceJobs[id] = nil
            self.manualPresence[id] = .init(label:stopConfirmed ? "接单已停止" :
                failed ? "监听中断 · 接单租约将过期" : "停止未确认 · 接单租约将过期",
                running:false,tasks:[])
        }
    }

    func stopManualPresence(_ id: UUID) {
        guard let job = manualPresenceJobs[id] else { return }
        manualPresence[id] = .init(label:"正在停止接单",running:true,tasks:[])
        job.cancel()
    }

    func stopManualPresenceAndWait(_ id: UUID) async {
        guard let job = manualPresenceJobs[id] else { return }
        stopManualPresence(id)
        await job.value
    }

    nonisolated private static func presenceTick(transport:AgentEnrollmentTransport,
            instance:AgentHubInstance,destination:AgentExecutionDestination,memberId:Int,
            session:String,action:String) async throws -> [ManualTask] {
        try await executorEnrollment.withExistingToken(instance:instance,destination:destination) { token in
            let client = try DedicatedCollaborationHub(url:transport.url,memberToken:token)
            let hub = InstanceBoundCollaborationHub(client:client,instance:instance,destination:destination,memberId:memberId)
            if action != "collaboration_presence_stop" {
                _ = try await localAgentAccounts.manualAPIHost(hub:hub,destination:destination,checkLease:true)
            }
            let args = try JSONSerialization.data(withJSONObject:["instance_id":instance.instanceId,"session_id":session])
            let data = try await hub.callOnce(name:action,arguments:args)
            let reply = try JSONDecoder().decode(PresenceReceipt.self,from:data)
            guard reply.instanceId == instance.instanceId,reply.memberId == memberId,reply.sessionId == session,
                  reply.accepting == (action != "collaboration_presence_stop"),reply.expiresAt.isFinite
            else { throw AgentEnrollmentError.receiptMismatch }
            if !reply.accepting { return [] }
            let query = try JSONSerialization.data(withJSONObject:["status":"open"])
            let bytes = try await hub.callOnce(name:"task_list",arguments:query)
            guard bytes.count <= 1_048_576 else { throw AgentAPIError.responseTooLarge }
            return try JSONDecoder().decode(ManualTasks.self,from:bytes).tasks.filter {
                $0.id > 0 && $0.status == "open" && $0.collaborationContextRequired
            }
        }
    }
}
