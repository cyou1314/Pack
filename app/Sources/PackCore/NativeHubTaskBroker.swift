import Foundation
import CoreFoundation

public enum NativeHubBrokerError: Error, Equatable {
    case invalidRequest, unavailable, unconfirmed
}

/// One explicitly selected native task, one authenticated Hub identity.
/// No generic tool bridge, credential export, or automatic mutation retry.
/// A future process transport must provide private bounded frames only.
public actor NativeHubTaskBroker {
    private let hub: any AgentCollaborationHub
    private let taskID: Int
    private let destination: AgentExecutionDestination
    private var sequence = 0
    private var busy = false
    private var closed = false
    private var claimed = false
    private var token: String?
    private var contextRead = false
    private var runAttempted = false
    private var running = false
    private var reads = 0

    public init(hub: any AgentCollaborationHub, taskID: Int, destination: AgentExecutionDestination) throws {
        guard taskID > 0, destination.accessKind == "native",
              ["chatgpt","gemini","doubao"].contains(destination.provider),
              let id = UUID(uuidString:destination.accountRef),
              id.uuidString.lowercased() == destination.accountRef
        else { throw NativeHubBrokerError.invalidRequest }
        self.hub = hub; self.taskID = taskID; self.destination = destination
    }

    private static func integer(_ value: Any?) -> Int? {
        guard let number = value as? NSNumber,
              CFGetTypeID(number) != CFBooleanGetTypeID(),
              let exact = Int(exactly:number.doubleValue),
              exact >= 0, exact <= 9_007_199_254_740_991 else { return nil }
        return exact
    }

    private static func object(_ data: Data, limit: Int) throws -> [String:Any] {
        guard data.count <= limit,
              let value = try JSONSerialization.jsonObject(with:data) as? [String:Any]
        else { throw NativeHubBrokerError.invalidRequest }
        return value
    }

    public func close() { closed = true }

    public func exchange(_ frame: Data) async throws -> Data {
        guard !closed, !busy, !Task.isCancelled else { throw NativeHubBrokerError.unavailable }
        let request = try Self.object(frame,limit:32*1024)
        guard Set(request.keys) == ["version","sequence","tool","arguments"],
              Self.integer(request["version"]) == 1,
              let seq = Self.integer(request["sequence"]), seq == sequence+1, seq <= 16,
              let tool = request["tool"] as? String,
              let args = request["arguments"] as? [String:Any],
              Self.integer(args["task_id"]) == taskID
        else { throw NativeHubBrokerError.invalidRequest }

        let keys = Set(args.keys)
        if tool == "task_claim" {
            guard !claimed, keys == ["task_id"] else { throw NativeHubBrokerError.invalidRequest }
            claimed = true
        } else {
            guard let token, args["claim_token"] as? String == token
            else { throw NativeHubBrokerError.invalidRequest }
            switch tool {
            case "collaboration_context_get":
                guard keys == ["task_id","claim_token"], reads < 4,
                      !runAttempted || running else { throw NativeHubBrokerError.invalidRequest }
                reads += 1
            case "run_start":
                guard contextRead,!runAttempted,keys == ["task_id","claim_token"]
                else { throw NativeHubBrokerError.invalidRequest }
                runAttempted = true
            case "task_complete":
                guard running, reads >= 2,keys == ["task_id","claim_token","result"],
                      let result = args["result"] as? String,
                      !result.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty,
                      result.utf8.count <= 16*1024 else { throw NativeHubBrokerError.invalidRequest }
                // One terminal mutation, even if the response is lost.
                closed = true
            case "task_block":
                let reasons: Set<String> = ["context_unavailable","profile_binding_mismatch",
                    "account_unavailable","egress_not_authorized","run_registration_unknown",
                    "context_changed","deadline_expired","provider_outcome_unknown",
                    "quota","provider_rejected","provider_unavailable"]
                guard keys == ["task_id","claim_token","reason","fail_class"],
                      args["fail_class"] as? String == "task",
                      let reason = args["reason"] as? String,reasons.contains(reason)
                else { throw NativeHubBrokerError.invalidRequest }
                closed = true
            default: throw NativeHubBrokerError.invalidRequest
            }
        }

        sequence = seq; busy = true
        defer { busy = false }
        do {
            let data = try JSONSerialization.data(withJSONObject:args)
            let response = try await hub.callOnce(name:tool,arguments:data)
            let value = try Self.object(response,limit:1024*1024)
            // Never expose arbitrary Hub response fields. Return only the
            // exact receipt or the scoped collaboration context.
            let result: [String:Any]
            switch tool {
            case "task_claim":
                guard Self.integer(value["task_id"]) == taskID else { throw NativeHubBrokerError.unconfirmed }
                if value["status"] as? String == "unavailable",
                   value["claim_token"] == nil,
                   let code = value["code"] as? String,
                   ["execution_capacity","execution_account_busy"].contains(code) {
                    closed = true
                    result = ["task_id":taskID,"status":"unavailable","code":code]
                } else {
                    guard value["status"] as? String == "claimed",
                          let received = value["claim_token"] as? String,
                          !received.isEmpty, received.utf8.count <= 256,
                          let expiry = value["lease_expires"] as? Double,expiry.isFinite
                    else { throw NativeHubBrokerError.unconfirmed }
                    token = received
                    result = ["task_id":taskID,"status":"claimed","claim_token":received,"lease_expires":expiry]
                }
            case "collaboration_context_get":
                let context = try JSONDecoder().decode(AgentCollaborationContext.self,from:response)
                guard context.destination == destination,context.maxBytes == 16*1024,
                      context.items.count <= 8 else { throw NativeHubBrokerError.unconfirmed }
                let encoded = try JSONEncoder().encode(context)
                guard encoded.count <= 32*1024 else { throw NativeHubBrokerError.unconfirmed }
                result = try Self.object(encoded,limit:32*1024)
                contextRead = true
            case "run_start":
                guard Self.integer(value["task_id"]) == taskID,
                      value["status"] as? String == "running",Self.integer(value["fence"]) == 1
                else { throw NativeHubBrokerError.unconfirmed }
                running = true
                result = ["task_id":taskID,"status":"running","fence":1]
            case "task_complete","task_block":
                let status = tool == "task_complete" ? "review" : "cancelled"
                guard Self.integer(value["task_id"]) == taskID,value["status"] as? String == status
                else { throw NativeHubBrokerError.unconfirmed }
                result = ["task_id":taskID,"status":status]
            default: throw NativeHubBrokerError.invalidRequest
            }
            try Task.checkCancellation()
            return try JSONSerialization.data(withJSONObject:["version":1,"sequence":seq,"result":result])
        } catch {
            if tool == "task_claim" { closed = true }
            // Context/start errors still allow the runner's one task_block
            // with the same confirmed claim token; no mutation retry.
            throw NativeHubBrokerError.unconfirmed
        }
    }
}
