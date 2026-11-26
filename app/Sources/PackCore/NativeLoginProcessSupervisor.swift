import Foundation
import Combine

/// Owns only the child process and its stop pipe. There is no shell, dynamic
/// command, browser credential, or captured page output. Child exit zero means
/// clean session close, NOT a verified provider login.
@MainActor public final class NativeLoginProcessSupervisor: ObservableObject {
    public enum Phase: Equatable, Sendable {
        case idle, starting, running, stopping, stopUnconfirmed, closed, recoveryRequired
        public var active: Bool {
            [.starting,.running,.stopping,.stopUnconfirmed].contains(self)
        }
    }
    @Published public private(set) var phase: Phase = .idle
    @Published public private(set) var accountID: UUID?
    private var child: Process?
    private var stopPipe: Pipe?
    private var generation = UUID()
    private var stopTimer: Task<Void,Never>?

    public init() {}

    /// Only trusted in-process caller may select the executable. The App
    /// supplies fixed signed-bundle locations, never user input or PATH lookup.
    public func launch(accountID: UUID, executable: URL, arguments: [String],
                       directory: URL, environment: [String:String]) throws {
        guard !phase.active,child == nil else { throw CocoaError(.fileWriteUnknown) }
        let process = Process(), pipe = Pipe()
        let ticket = UUID()
        generation = ticket
        self.accountID = accountID
        phase = .starting
        process.executableURL = executable
        process.arguments = arguments
        process.currentDirectoryURL = directory
        process.environment = environment
        process.standardInput = pipe
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        process.terminationHandler = { [weak self] finished in
            let clean = finished.terminationReason == .exit && finished.terminationStatus == 0
            Task { @MainActor [weak self] in self?.didExit(ticket:ticket,clean:clean) }
        }
        child = process
        stopPipe = pipe
        do {
            try process.run()
            phase = .running
        } catch {
            try? pipe.fileHandleForWriting.close()
            child = nil; stopPipe = nil; phase = .recoveryRequired
            throw error
        }
    }

    public func stop() {
        guard child != nil,phase == .running || phase == .starting else { return }
        phase = .stopping
        // EOF, not a signal or arbitrary message. No force-kill after timeout;
        // an uncertain browser may still own its on-disk profile lease.
        try? stopPipe?.fileHandleForWriting.close()
        stopPipe = nil
        let ticket = generation
        stopTimer = Task { [weak self] in
            try? await Task.sleep(for:.seconds(45))
            guard !Task.isCancelled,let self,self.generation == ticket,self.child != nil else { return }
            self.phase = .stopUnconfirmed
        }
    }

    private func didExit(ticket: UUID, clean: Bool) {
        guard generation == ticket,child != nil else { return }
        stopTimer?.cancel(); stopTimer = nil
        try? stopPipe?.fileHandleForWriting.close()
        stopPipe = nil; child = nil
        phase = clean ? .closed : .recoveryRequired
    }

    deinit {
        // Best effort EOF; no lease deletion or process-tree kill.
        try? stopPipe?.fileHandleForWriting.close()
        stopTimer?.cancel()
    }
}
