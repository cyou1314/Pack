import Foundation
import Darwin

public enum NativeTaskChannelError: Error, Equatable {
    case invalidConfiguration, invalidFrame, ioUnavailable, deadline, childUnconfirmed
}

/// Private stdin/stdout framing for a trusted, explicitly selected child.
/// No shell, listening port, or long-lived credential crosses the channel.
/// This is not a sandbox. A child may still own browser/account leases after
/// EOF or a failed exit: never clear them or auto-restart the task.
public enum NativeTaskProcessChannel {
    private static let requestLimit = 32*1024
    private static let responseLimit = 64*1024

    private static func ready(_ fd: Int32, _ events: Int16, _ deadline: UInt64) throws {
        while true {
            let now = DispatchTime.now().uptimeNanoseconds
            guard now < deadline else { throw NativeTaskChannelError.deadline }
            let ms = Int32(min(1000,max(1,(deadline-now)/1_000_000)))
            var p = pollfd(fd:fd,events:events,revents:0)
            let result = Darwin.poll(&p,1,ms)
            if result < 0 {
                if errno == EINTR { continue }
                throw NativeTaskChannelError.ioUnavailable
            }
            if result == 0 { continue }
            if p.revents & (events | Int16(POLLHUP)) != 0 { return }
            throw NativeTaskChannelError.ioUnavailable
        }
    }

    // Read exactly count bytes, or a clean EOF before a new frame header.
    private static func read(_ fd: Int32, count: Int, deadline: UInt64, eofAllowed: Bool = false) throws -> Data? {
        var bytes = Data(count:count)
        var offset = 0
        while offset < count {
            try ready(fd,Int16(POLLIN),deadline)
            let n = bytes.withUnsafeMutableBytes { raw in
                Darwin.read(fd,raw.baseAddress!.advanced(by:offset),count-offset)
            }
            if n == 0 {
                if eofAllowed && offset == 0 { return nil }
                throw NativeTaskChannelError.invalidFrame
            }
            if n < 0 {
                if errno == EINTR || errno == EAGAIN { continue }
                throw NativeTaskChannelError.ioUnavailable
            }
            offset += n
        }
        return bytes
    }

    private static func readFrame(_ fd: Int32, deadline: UInt64) throws -> Data? {
        guard let header = try read(fd,count:4,deadline:deadline,eofAllowed:true) else { return nil }
        let count = header.reduce(0) { ($0 << 8) | Int($1) }
        guard (1...requestLimit).contains(count) else { throw NativeTaskChannelError.invalidFrame }
        return try read(fd,count:count,deadline:deadline)
    }

    private static func writeFrame(_ fd: Int32, data: Data, deadline: UInt64) throws {
        guard (1...responseLimit).contains(data.count) else { throw NativeTaskChannelError.invalidFrame }
        var count = UInt32(data.count).bigEndian
        var frame = withUnsafeBytes(of:&count) { Data($0) }
        frame.append(data)
        var offset = 0
        while offset < frame.count {
            try ready(fd,Int16(POLLOUT),deadline)
            let n = frame.withUnsafeBytes { raw in
                Darwin.write(fd,raw.baseAddress!.advanced(by:offset),frame.count-offset)
            }
            if n < 0 && (errno == EINTR || errno == EAGAIN) { continue }
            guard n > 0 else { throw NativeTaskChannelError.ioUnavailable }
            offset += n
        }
    }

    // Blocking POSIX I/O lives on a dedicated queue, not the UI or Swift's
    // cooperative executor. A per-exchange monotonic deadline bounds pipe
    // stalls. The authenticated Hub transport has its own network timeout.
    private static func io<T: Sendable>(_ operation: @escaping @Sendable () throws -> T) async throws -> T {
        try await withCheckedThrowingContinuation { continuation in
            DispatchQueue.global(qos:.utility).async {
                continuation.resume(with:Result { try operation() })
            }
        }
    }

    private static func configure(_ fd: Int32, writing: Bool) throws {
        let flags = fcntl(fd,F_GETFL)
        guard flags >= 0,fcntl(fd,F_SETFL,flags | O_NONBLOCK) == 0,
              !writing || fcntl(fd,F_SETNOSIGPIPE,1) == 0
        else { throw NativeTaskChannelError.ioUnavailable }
    }

    /// One child for one task. Only trusted caller chooses executable and
    /// program; arguments/environment must be fixed non-secret metadata.
    /// Returns only a confirmed clean child exit, not proof of remote success.
    public static func run(executable: URL, arguments: [String], directory: URL,
                           environment: [String:String], broker: NativeHubTaskBroker,
                           timeoutSeconds: Int = 120) async throws {
        guard executable.isFileURL,directory.isFileURL,(1...300).contains(timeoutSeconds)
        else { throw NativeTaskChannelError.invalidConfiguration }
        let child = Process(), input = Pipe(), output = Pipe()
        child.executableURL = executable; child.arguments = arguments
        child.currentDirectoryURL = directory; child.environment = environment
        child.standardInput = input; child.standardOutput = output
        child.standardError = FileHandle.nullDevice
        let writer = input.fileHandleForWriting, reader = output.fileHandleForReading
        let deadline = DispatchTime.now().uptimeNanoseconds + UInt64(timeoutSeconds)*1_000_000_000
        var failure: Error?
        do {
            try configure(writer.fileDescriptor,writing:true)
            try configure(reader.fileDescriptor,writing:false)
            try Task.checkCancellation()
            try child.run()
            // Foundation closes the child-side handles when passed a Pipe.
            for _ in 0..<17 {
                try Task.checkCancellation()
                let frame = try await io { try readFrame(reader.fileDescriptor,deadline:deadline) }
                guard let frame else { break }
                let response: Data
                do { response = try await broker.exchange(frame) }
                catch {
                    // A fixed error frame carries no raw exception or secret.
                    // Known claim may still be stopped after a failed read/start.
                    response = Data("{\"error\":\"unconfirmed\"}".utf8)
                }
                try Task.checkCancellation()
                try await io { try writeFrame(writer.fileDescriptor,data:response,deadline:deadline) }
            }
            // A compliant child sends at most 16 frames then closes stdout.
            // Further frames are not served; a still-running child is uncertain.
        } catch { failure = error }
        await broker.close()
        try? writer.close()
        try? reader.close()
        // EOF requests shutdown. No signal, process-tree kill or lease removal.
        // A child still running after the bounded grace period is unconfirmed.
        if child.processIdentifier != 0 {
            for _ in 0..<50 where child.isRunning {
                try? await Task.sleep(for:.milliseconds(100))
            }
            guard !child.isRunning,child.terminationReason == .exit,child.terminationStatus == 0
            else { throw NativeTaskChannelError.childUnconfirmed }
        }
        if let failure { throw failure }
    }
}
