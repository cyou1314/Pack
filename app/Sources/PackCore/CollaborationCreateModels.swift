import Foundation

public enum CollaborationMode: String, CaseIterable, Identifiable, Sendable {
    case focus, compare, team
    public var id: String { rawValue }
    public var maxWorkers: Int {
        switch self { case .focus: return 0; case .compare: return 2; case .team: return 3 }
    }
}

public struct CollaborationRouteOptions: Equatable, Sendable {
    public var mode: CollaborationMode = .focus
    public var workers: Int = 1
    public var maxCalls: Int = 5
    public var allowUnknownQuota = false
    public init() {}
    public var plannedCalls: Int { mode == .focus ? 1 : workers + 2 }
    /// Structural waves, not timing estimates. Each wave still needs
    /// available accounts, execution admission and owner review.
    public var firstWaveCalls: Int {
        switch mode { case .focus: return 1; case .compare: return workers+1; case .team: return workers }
    }
    public var sequentialWaves: Int {
        switch mode { case .focus: return 1; case .compare: return 2; case .team: return 3 }
    }
    public var valid: Bool {
        (1...8).contains(maxCalls) && plannedCalls <= maxCalls &&
        (mode == .focus || (1...mode.maxWorkers).contains(workers))
    }
}

public struct CollaborationRouteCandidate: Decodable, Identifiable, Sendable {
    public var id: Int { memberId }
    public let memberId: Int
    public let memberName: String?
    public let provider: String
    public let accessKind: String
    public let online: Bool
    public let queue: Int
    public let activePlan: Bool
    public let quotaState: String
    // Optional for compatibility with older Hub previews. Missing is not
    // proof of verified login; the actual claim gate remains authoritative.
    public let executionAllowed: Bool?
    enum CodingKeys: String, CodingKey {
        case memberId = "member_id", memberName = "member_name", provider, accessKind = "access_kind"
        case online, queue, activePlan = "active_plan", quotaState = "quota_state"
        case executionAllowed = "execution_allowed"
    }
    public func eligible(allowUnknown: Bool) -> Bool {
        memberId > 0 && online && queue == 0 && !activePlan && executionAllowed != false &&
        (quotaState == "available" || (allowUnknown && quotaState == "unknown"))
    }
}

public struct CollaborationRouteSuggestion: Decodable, Equatable, Sendable {
    public let leader: Int
    public let workers: [Int]
    public let reviewer: Int?
    public let plannedCalls: Int
    enum CodingKeys: String, CodingKey { case leader, workers, reviewer, plannedCalls = "planned_calls" }
    public var members: [Int] { [leader] + workers + (reviewer.map { [$0] } ?? []) }
}

public struct CollaborationRoutePreview: Decodable, Sendable {
    public let mode: String
    public let suggestion: CollaborationRouteSuggestion?
    public let candidates: [CollaborationRouteCandidate]
    public let requiresOwnerConfirmation: Bool
    enum CodingKeys: String, CodingKey {
        case mode, suggestion, candidates, requiresOwnerConfirmation = "requires_owner_confirmation"
    }
    public func accepts(_ options: CollaborationRouteOptions) -> Bool {
        guard options.valid, mode == options.mode.rawValue, requiresOwnerConfirmation,
              let s = suggestion, s.plannedCalls == options.plannedCalls,
              s.members.allSatisfy({ $0 > 0 }), Set(s.members).count == s.members.count,
              Set(candidates.map(\.id)).count == candidates.count,
              s.members.allSatisfy({ id in candidates.contains {
                  $0.id == id && $0.eligible(allowUnknown: options.allowUnknownQuota)
              } }) else { return false }
        switch options.mode {
        case .focus: return s.workers.isEmpty && s.reviewer == nil
        case .compare: return s.workers.count == options.workers && s.reviewer == nil
        case .team: return s.workers.count == options.workers && s.reviewer != nil
        }
    }
}

/// The retry payload is immutable. A new key must never be generated solely
/// because a response was lost. No credentials or prompt are persisted.
public struct CollaborationCreateRequest: Sendable {
    public let room: String
    public let requestKey: String
    public let body: String
    public let options: CollaborationRouteOptions
    public let route: CollaborationRouteSuggestion
    public let deadline: Double

    public init?(room: String, body: String, options: CollaborationRouteOptions,
                 preview: CollaborationRoutePreview, durationMinutes: Int, now: Date = Date(),
                 id: UUID = UUID()) {
        guard !room.isEmpty, !body.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              body.utf8.count <= 12*1024, (5...1440).contains(durationMinutes),
              preview.accepts(options), let route = preview.suggestion else { return nil }
        self.room = room
        self.body = body
        self.options = options
        self.route = route
        self.deadline = now.addingTimeInterval(Double(durationMinutes)*60).timeIntervalSince1970
        self.requestKey = id.uuidString.replacingOccurrences(of: "-", with: "").lowercased()
    }
}

public struct CollaborationCreateReceipt: Decodable, Sendable {
    public let runId: String
    public let status: String
    public let maxCalls: Int
    public let deadline: Double
    enum CodingKeys: String, CodingKey { case runId = "run_id", status, maxCalls = "max_calls", deadline }
    public func matches(_ request: CollaborationCreateRequest) -> Bool {
        !runId.isEmpty && ["active","succeeded","failed","cancelled","expired"].contains(status) &&
        maxCalls == request.options.maxCalls && abs(deadline-request.deadline) < 0.001
    }
}

public struct CollaborationRequestLookup: Decodable, Sendable {
    public let room: String
    public let requestKey: String
    public let runId: String?
    enum CodingKeys: String, CodingKey { case room, requestKey = "request_key", runId = "run_id" }
}
