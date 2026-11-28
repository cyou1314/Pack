import Foundation

public struct CollaborationWorkspacePage: Decodable, Sendable {
    public let room: String
    public let runs: [CollaborationWorkspaceRun]
    public let nextBeforeSourceId: Int?
    enum CodingKeys: String, CodingKey {
        case room, runs
        case nextBeforeSourceId = "next_before_source_id"
    }
}

public struct CollaborationWorkspaceRun: Decodable, Identifiable, Sendable {
    public var id: String { runId }
    public let runId: String
    public let mode: String
    public let status: String
    public let callsUsed: Int
    public let maxCalls: Int
    public let deadline: Double
    public let sourceMessageId: Int
    public let roomId: Int
    public let steps: [CollaborationWorkspaceStep]
    public let finalResultAvailable: Bool
    public let publication: CollaborationPublication?
    public let contextBudgets: [CollaborationContextBudget]?
    enum CodingKeys: String, CodingKey {
        case mode, status, deadline, steps, publication
        case runId = "run_id", callsUsed = "calls_used", maxCalls = "max_calls"
        case sourceMessageId = "source_message_id", roomId = "room_id"
        case finalResultAvailable = "final_result_available"
        case contextBudgets = "context_budgets"
    }
}

/// Hub projection, not a context grant or token estimate.
public struct CollaborationContextBudget: Decodable, Identifiable, Sendable {
    public var id: String { stepKey }
    public let stepKey: String
    public let maxBytes: Int
    public let minimumBytes: Int
    public let missingResults: Int
    public let unapprovedResults: Int
    public let dependencyCount: Int
    public let suggestedEncodedTextBytes: Int
    public let overBudget: Bool
    enum CodingKeys: String, CodingKey {
        case stepKey = "step_key", maxBytes = "max_bytes", minimumBytes = "minimum_bytes"
        case missingResults = "missing_results", unapprovedResults = "unapproved_results"
        case dependencyCount = "dependency_count", suggestedEncodedTextBytes = "suggested_encoded_text_bytes"
        case overBudget = "over_budget"
    }
}

public struct CollaborationWorkspaceStep: Decodable, Identifiable, Sendable {
    public var id: String { key }
    public let key: String
    public let member: String
    public let role: String
    public let status: String
    public let taskId: Int?
    public let taskStatus: String?
    public let updatedAt: Double?
    public let hasResult: Bool
    enum CodingKeys: String, CodingKey {
        case key, member, role, status
        case taskId = "task_id", taskStatus = "task_status", updatedAt = "updated_at", hasResult = "has_result"
    }
}

public struct CollaborationPublication: Decodable, Sendable {
    public let messageId: Int
    public let publishedAt: Double
    enum CodingKeys: String, CodingKey { case messageId = "message_id", publishedAt = "published_at" }
}

public struct CollaborationWorkspaceResult: Decodable, Sendable {
    public let runId: String
    public let stepKey: String?
    public let approved: Bool
    public let result: String
    public let sha256: String?
    public let reviewVersion: String?
    public let contextBudgets: [CollaborationContextBudget]?
    enum CodingKeys: String, CodingKey {
        case runId = "run_id", stepKey = "step_key", approved, result, sha256
        case reviewVersion = "review_version"
        case contextBudgets = "context_budgets"
    }
    public var canReview: Bool {
        !approved && stepKey != nil && !result.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
        reviewVersion?.range(of: "^[a-f0-9]{64}$", options: .regularExpression) != nil
    }
    public var canPublishFinal: Bool {
        approved && stepKey == nil && !result.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
        sha256?.range(of: "^[a-f0-9]{64}$", options: .regularExpression) != nil
    }
}

public struct CollaborationWorkspaceUsage: Decodable, Sendable {
    public let runId: String
    public let registeredAttempts: Int
    public let meteredAttempts: Int
    public let unmeteredRegisteredAttempts: Int
    public let reportedInputTokens: Int?
    public let reportedOutputTokens: Int?
    enum CodingKeys: String, CodingKey {
        case runId = "run_id", registeredAttempts = "registered_attempts", meteredAttempts = "metered_attempts"
        case unmeteredRegisteredAttempts = "unmetered_registered_attempts"
        case reportedInputTokens = "reported_input_tokens", reportedOutputTokens = "reported_output_tokens"
    }
}
public struct CollaborationPublishReceipt: Decodable, Sendable {
    public let runId: String
    public let messageId: Int
    public let published: Bool
    enum CodingKeys: String, CodingKey { case runId = "run_id", messageId = "message_id", published }
}


public struct CollaborationReviewReceipt: Decodable, Sendable {
    public let runId: String
    public let stepKey: String
    public let taskId: Int
    public let status: String
    public let runStatus: String
    enum CodingKeys: String, CodingKey {
        case runId = "run_id", stepKey = "step_key", taskId = "task_id", status, runStatus = "run_status"
    }
}
public struct CollaborationControlStatus: Decodable, Sendable {
    public let runId: String
    public let status: String
    enum CodingKeys: String, CodingKey { case runId = "run_id", status }
}

public extension CollaborationWorkspaceRun {
    /// Only an entire outstanding parallel wave, never final/review roles.
    var batchReviewKeys: [String] {
        guard status == "active" else { return [] }
        let role = mode == "compare" ? "independent-analysis" : mode == "team" ? "worker" : ""
        guard !role.isEmpty else { return [] }
        let wave = steps.filter { $0.role == role }
        let pending = wave.filter { $0.status != "succeeded" }
        guard (2...3).contains(pending.count),
              Set(pending.map(\.key)).count == pending.count,
              wave.allSatisfy({ $0.status != "succeeded" || $0.taskStatus == "done" }),
              pending.allSatisfy({ $0.status == "running" && $0.taskStatus == "review" && $0.hasResult })
        else { return [] }
        return pending.map(\.key).sorted()
    }
}

public struct CollaborationBatchReviewReceipt: Decodable, Sendable {
    public let runId: String
    public let approvedSteps: [String]
    public let runStatus: String
    enum CodingKeys: String, CodingKey {
        case runId = "run_id", approvedSteps = "approved_steps", runStatus = "run_status"
    }
    public func matches(run: String, keys: [String]) -> Bool {
        runId == run && runStatus == "active" &&
        approvedSteps.count == keys.count && Set(approvedSteps) == Set(keys)
    }
}
