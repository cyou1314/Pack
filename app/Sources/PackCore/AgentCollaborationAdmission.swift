import Foundation

/// Process-local admission for one verified Hub instance. No Provider I/O,
/// no task claims, and no automatic retry or eviction of attempted tasks.
public actor AgentCollaborationAdmission {
    public struct Snapshot: Sendable, Equatable {
        public let accepting: Bool
        public let active: Int
        public let tracked: Int
        public let maxConcurrent: Int
        public let outcomes: [String:Int]
    }

    private let maxConcurrent: Int
    private let maxTracked: Int
    private var active: Set<Int> = []
    private var completed: [Int:String] = [:]
    private var stopping = false

    public init(maxConcurrent: Int = 4, maxTracked: Int = 4096) {
        // Invalid configuration fails closed instead of increasing capacity.
        if (1...32).contains(maxConcurrent),
           (maxConcurrent...100000).contains(maxTracked) {
            self.maxConcurrent = maxConcurrent
            self.maxTracked = maxTracked
        } else {
            self.maxConcurrent = 0
            self.maxTracked = 0
            self.stopping = true
        }
    }

    func begin(_ taskID: Int) -> Bool {
        guard taskID > 0, !stopping, active.count < maxConcurrent,
              active.count + completed.count < maxTracked,
              !active.contains(taskID), completed[taskID] == nil else { return false }
        active.insert(taskID)
        return true
    }

    func finish(_ taskID: Int, result: AgentCollaborationExecutionResult) {
        guard active.remove(taskID) != nil else { return }
        // The API host returns unavailable only before any task_claim.
        // It is safe to release that admission without recording an attempt.
        switch result {
        case .unavailable: return
        case .claimUnknown: completed[taskID] = "claimUnknown"
        case .blocked(let code, _):
            completed[taskID] = code == "provider_outcome_unknown" ? "providerUnknown" : "blocked"
        case .stopUnknown(let code, _):
            completed[taskID] = code == "provider_outcome_unknown" ? "providerUnknown" : "stopUnknown"
        case .review: completed[taskID] = "review"
        case .deliveryUnknown: completed[taskID] = "deliveryUnknown"
        }
    }

    public func stopAdmission() { stopping = true }

    public func snapshot() -> Snapshot {
        var counts: [String:Int] = [:]
        for value in completed.values { counts[value,default:0] += 1 }
        return .init(accepting: !stopping && active.count < maxConcurrent &&
                       active.count + completed.count < maxTracked,
                     active: active.count, tracked: completed.count,
                     maxConcurrent: maxConcurrent, outcomes: counts)
    }
}
