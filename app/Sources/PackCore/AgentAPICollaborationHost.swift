import Foundation
import Darwin

/// Same directory marker as Node AccountExecutionLeases. The trusted host
/// supplies a common pre-created 0700 directory. Never break a stale lease.
public struct AgentSharedAccountLease: Sendable {
    private let root: URL
    private let marker: URL
    private let rootDevice: dev_t
    private let rootInode: ino_t
    private let device: dev_t
    private let inode: ino_t

    public enum LeaseError: Error { case unavailable, unsafeDirectory, changed }

    private static func info(_ url: URL) throws -> stat {
        var value = stat()
        guard url.isFileURL, lstat(url.path, &value) == 0,
              (value.st_mode & S_IFMT) == S_IFDIR,
              (value.st_mode & 0o077) == 0, value.st_uid == getuid()
        else { throw LeaseError.unsafeDirectory }
        return value
    }

    public init(root: URL, accountRef: String) throws {
        guard let id = UUID(uuidString: accountRef), id.uuidString.lowercased() == accountRef
        else { throw LeaseError.unavailable }
        _ = try Self.info(root)
        let real = root.resolvingSymlinksInPath()
        let rootInfo = try Self.info(real)
        let marker = real.appendingPathComponent(accountRef + ".lease", isDirectory: true)
        guard mkdir(marker.path, 0o700) == 0 else { throw LeaseError.unavailable }
        // Any subsequent failure retains the marker.
        let info = try Self.info(marker)
        self.root = real
        self.marker = marker
        self.rootDevice = rootInfo.st_dev
        self.rootInode = rootInfo.st_ino
        self.device = info.st_dev
        self.inode = info.st_ino
    }

    public func release() throws {
        let r = try Self.info(root), m = try Self.info(marker)
        guard r.st_dev == rootDevice, r.st_ino == rootInode,
              m.st_dev == device, m.st_ino == inode else { throw LeaseError.changed }
        guard rmdir(marker.path) == 0 else { throw LeaseError.changed }
    }
}

/// Transport must use an explicitly dedicated member credential, and must
/// never retry a mutation. No dispatcher/owner credential fallback.
public protocol AgentCollaborationHub: Sendable {
    func callOnce(name: String, arguments: Data) async throws -> Data
}

public struct AgentCollaborationContext: Codable, Equatable, Sendable {
    public let runID: String
    public let stepKey: String
    public let provider: String
    public let accessKind: String
    public let accountRef: String
    public let deadline: Double
    public let maxBytes: Int
    public let role: AgentCollaborationRole
    public let maxOutputTokens: Int
    public let items: [AgentAPIContextItem]
    enum CodingKeys: String, CodingKey {
        case runID = "run_id", stepKey = "step_key", provider
        case accessKind = "access_kind", accountRef = "account_ref", deadline
        case maxBytes = "max_bytes", items, role
        case maxOutputTokens = "max_output_tokens"
    }
    var destination: AgentExecutionDestination {
        .init(accountRef: accountRef, provider: provider, accessKind: accessKind)
    }
}

public enum AgentCollaborationExecutionResult: Equatable, Sendable {
    case unavailable
    case claimUnknown
    case blocked(code: String, usage: AgentAPIUsage?)
    case stopUnknown(code: String, usage: AgentAPIUsage?)
    case review(usage: AgentAPIUsage?)
    case deliveryUnknown(usage: AgentAPIUsage?)
}

private struct Claim: Decodable {
    let taskID: Int
    let status: String
    let token: String
    let expires: Double
    enum CodingKeys: String, CodingKey {
        case taskID = "task_id", status, token = "claim_token", expires = "lease_expires"
    }
}
private struct Receipt: Decodable {
    let taskID: Int
    let status: String
    let fence: Int?
    enum CodingKeys: String, CodingKey { case taskID = "task_id", status, fence }
}

/// API lane runs entirely inside the trusted Swift host (no key IPC). Native
/// lane keeps the existing Node runner. Same Hub contract and shared lease
/// namespace. No background polling or automatic production activation.
public actor AgentAPICollaborationHost {
    private let hub: any AgentCollaborationHub
    private let resolver: AgentLocalAccountResolver
    private let adapter: AgentOfficialAPIAdapter
    private let leaseRoot: URL
    private let expected: AgentExecutionDestination
    private var executing = false

    public init(hub: any AgentCollaborationHub, registry: AgentRegistryStore,
                vault: AgentCredentialVault = .init(),
                transport: any AgentHTTPTransport = AgentURLSessionTransport(),
                leaseRoot: URL, expected: AgentExecutionDestination) {
        self.hub = hub
        self.resolver = .init(registry: registry, vault: vault)
        self.adapter = .init(registry: registry, vault: vault, transport: transport)
        self.leaseRoot = leaseRoot
        self.expected = expected
    }

    private func call<T: Decodable>(_ name: String, _ args: [String: Any] = [:]) async throws -> T {
        let data = try JSONSerialization.data(withJSONObject: args)
        let response = try await hub.callOnce(name: name, arguments: data)
        guard response.count <= 1_048_576 else { throw AgentAPIError.responseTooLarge }
        return try JSONDecoder().decode(T.self, from: response)
    }

    private func stop(_ taskID: Int, _ token: String, _ code: String,
                      _ usage: AgentAPIUsage? = nil) async -> AgentCollaborationExecutionResult {
        do {
            var args: [String: Any] = [
                "task_id": taskID, "claim_token": token, "reason": code,
                // HTTP 429 is not evidence of a weekly quota at 100%.
                "fail_class": "task"
            ]
            if let usage = usage {
                args["provider_usage"] = ["input_tokens": usage.inputTokens, "output_tokens": usage.outputTokens]
            }
            let receipt: Receipt = try await call("task_block", args)
            if receipt.taskID == taskID && receipt.status == "cancelled" {
                return .blocked(code: code, usage: usage)
            }
        } catch {}
        return .stopUnknown(code: code, usage: usage)
    }

    public func execute(taskID: Int, maxOutputTokens: Int,
                        authorize: @escaping @Sendable (AgentAPIAuthorization) async throws -> Bool)
        async -> AgentCollaborationExecutionResult {
        guard !executing, taskID > 0, (1...8192).contains(maxOutputTokens),
              expected.accessKind == "api", !Task.isCancelled else { return .unavailable }
        executing = true
        defer { executing = false }
        // Confirm dedicated server identity BEFORE claiming or reading context.
        do {
            let actual: AgentExecutionDestination = try await call("collaboration_executor_info")
            guard actual == expected,
                  case .apiCredentialPresent = try await resolver.resolve(expected)
            else { return .unavailable }
        } catch { return .unavailable }

        let lease: AgentSharedAccountLease
        do { lease = try .init(root: leaseRoot, accountRef: expected.accountRef) }
        catch { return .unavailable }
        var retain = false
        defer { if !retain { try? lease.release() } }

        let claim: Claim
        do { claim = try await call("task_claim", ["task_id": taskID]) }
        catch { return .claimUnknown }
        guard claim.taskID == taskID, claim.status == "claimed", !claim.token.isEmpty,
              claim.expires.isFinite else { return .claimUnknown }
        let args: [String: Any] = ["task_id": taskID, "claim_token": claim.token]
        let initial: AgentCollaborationContext
        do {
            initial = try await call("collaboration_context_get", args)
            guard initial.destination == expected, !initial.runID.isEmpty, !initial.stepKey.isEmpty,
                  initial.maxBytes == 16*1024, initial.deadline.isFinite,
                  initial.role.matches(stepKey: initial.stepKey), initial.maxOutputTokens == initial.role.outputLimit,
                  min(initial.deadline, claim.expires) > Date().timeIntervalSince1970
            else { return await stop(taskID, claim.token, "context_unavailable") }
        } catch { return await stop(taskID, claim.token, "context_unavailable") }
        do {
            let registered: Receipt = try await call("run_start", args)
            guard registered.taskID == taskID, registered.status == "running", registered.fence == 1
            else { return await stop(taskID, claim.token, "run_registration_unknown") }
        } catch { return await stop(taskID, claim.token, "run_registration_unknown") }

        let deadline = Date(timeIntervalSince1970: min(initial.deadline, claim.expires))
        let outcome: AgentAPIOutcome
        // Unknown send or delivery must retain the on-disk account marker.
        retain = true
        do {
            outcome = try await adapter.execute(destination: expected, items: initial.items,
                maxOutputTokens: min(maxOutputTokens,initial.maxOutputTokens), deadline: deadline, role: initial.role,
                authorize: { [self] approval in
                    guard try await authorize(approval), !Task.isCancelled else { return false }
                    // Re-check after human approval, before actual send. The
                    // exact prepared request is bound by the adapter's digest.
                    let current: AgentCollaborationContext = try await self.call("collaboration_context_get", [
                        "task_id": taskID, "claim_token": claim.token
                    ])
                    return current == initial && Date() < deadline
                })
        } catch let error as AgentAPIError where error == .unauthorized ||
                    error == .invalidRequest || error == .identityMismatch || error == .accountUnavailable {
            let stopped = await stop(taskID, claim.token, "provider_rejected")
            if case .blocked = stopped { retain = false }
            return stopped
        } catch {
            return await stop(taskID, claim.token, "provider_outcome_unknown")
        }
        switch outcome {
        case .failed(let code, let usage):
            let stopped = await stop(taskID, claim.token, code, usage)
            if case .blocked = stopped { retain = false }
            return stopped
        case .succeeded(let text, let usage):
            do {
                var args: [String: Any] = [
                    "task_id": taskID, "claim_token": claim.token, "result": text
                ]
                if let usage = usage {
                    args["provider_usage"] = ["input_tokens": usage.inputTokens, "output_tokens": usage.outputTokens]
                }
                let receipt: Receipt = try await call("task_complete", args)
                if receipt.taskID == taskID && receipt.status == "review" {
                    retain = false
                    return .review(usage: usage)
                }
            } catch {}
            // No second submission, no task_block after uncertain commit.
            return .deliveryUnknown(usage: usage)
        }
    }
}
