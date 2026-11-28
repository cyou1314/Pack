import Foundation
import Combine

public struct NativeTaskRunReport: Sendable, Equatable {
    public let receipt: NativeTaskBrokerReceipt?
    public let childExitConfirmed: Bool
    public init(receipt: NativeTaskBrokerReceipt?, childExitConfirmed: Bool) {
        self.receipt = receipt
        self.childExitConfirmed = childExitConfirmed
    }
}

/// One controller for one explicitly selected task. No persistent approval
/// cache, automatic restart, or claim that stopping cancels the remote send.
@MainActor public final class NativeTaskExecutionController: ObservableObject {
    public enum Phase: Equatable, Sendable { case idle, running, stopping, finished }
    public struct Pending: Identifiable, Sendable {
        public let id: UUID
        public let approval: NativeTaskApproval
        public let expires: Date
    }
    public typealias ApprovalHandler = @Sendable (NativeTaskApproval) async throws -> Bool
    @Published public private(set) var phase: Phase = .idle
    @Published public private(set) var pending: Pending?
    @Published public private(set) var report: NativeTaskRunReport?
    public let taskID: Int
    public let destination: AgentExecutionDestination
    private let scopeCurrent: @MainActor () -> Bool
    private let approvalTimeout: TimeInterval
    private var job: Task<Void,Never>?
    private var timer: Task<Void,Never>?
    private var waiter: CheckedContinuation<Bool,Never>?

    public init(taskID: Int, destination: AgentExecutionDestination,
                approvalTimeout: TimeInterval = 60,
                scopeCurrent: @escaping @MainActor () -> Bool) {
        self.taskID = taskID; self.destination = destination
        self.scopeCurrent = scopeCurrent
        self.approvalTimeout = approvalTimeout.isFinite && approvalTimeout > 0
            ? min(approvalTimeout,120) : 0
    }

    /// Trusted host chooses the fixed worker; no executable selection by model.
    @discardableResult public func start(
        operation: @escaping @Sendable (ApprovalHandler) async throws -> NativeTaskRunReport
    ) -> Bool {
        guard phase == .idle,taskID > 0,destination.accessKind == "native",
              scopeCurrent(),approvalTimeout > 0 else { return false }
        phase = .running
        job = Task { [self] in
            let result: NativeTaskRunReport
            do {
                result = try await operation { [weak self] approval in
                    guard let self else { return false }
                    return await self.request(approval)
                }
            } catch {
                result = .init(receipt:nil,childExitConfirmed:false)
            }
            if let id = pending?.id { resolve(id,allow:false) }
            report = result
            phase = .finished
            job = nil
        }
        return true
    }

    private func request(_ approval: NativeTaskApproval) async -> Bool {
        let id = UUID()
        let context: AgentCollaborationContext
        let selected: Int
        let deadline: Double
        switch approval {
        case .context(let task,let value):
            selected = task; context = value; deadline = value.deadline
        case .send(let task,let value,_,_,_,_,let end):
            selected = task; context = value; deadline = min(end,value.deadline)
        }
        guard phase == .running,waiter == nil,scopeCurrent(),
              selected == taskID,context.destination == destination,
              deadline.isFinite,!Task.isCancelled else { return false }
        let interval = min(approvalTimeout,deadline-Date().timeIntervalSince1970)
        guard interval > 0 else { return false }
        let expires = Date().addingTimeInterval(interval)
        return await withTaskCancellationHandler {
            await withCheckedContinuation { continuation in
                guard !Task.isCancelled,phase == .running,scopeCurrent(),waiter == nil,
                      expires > Date() else { continuation.resume(returning:false); return }
                waiter = continuation
                pending = .init(id:id,approval:approval,expires:expires)
                timer = Task { [weak self] in
                    do { try await Task.sleep(for:.seconds(interval)) } catch { return }
                    self?.resolve(id,allow:false)
                }
            }
        } onCancel: {
            Task { @MainActor [weak self] in self?.resolve(id,allow:false) }
        }
    }

    /// Ticket prevents a stale button or delayed timer approving a later prompt.
    public func decide(_ id: UUID, allow: Bool) { resolve(id,allow:allow) }

    private func resolve(_ id: UUID, allow: Bool) {
        guard let value = pending,value.id == id,let continuation = waiter else { return }
        let approved = allow && phase == .running && scopeCurrent() && value.expires > Date()
        pending = nil; waiter = nil
        timer?.cancel(); timer = nil
        continuation.resume(returning:approved)
    }

    public func stop() {
        guard phase == .running else { return }
        phase = .stopping
        if let id = pending?.id { resolve(id,allow:false) }
        job?.cancel()
    }

    public func scopeDidChange() {
        if !scopeCurrent() { stop() }
    }
}
