import Foundation
import Security

/// OpenAI Responses API adapter. API credentials are resolved from Keychain
/// only; no secret is stored in AgentProfile or emitted in diagnostics.
actor OpenAIResponsesAdapter: AgentAdapter {
    nonisolated let providerID = "openai"
    nonisolated let mode = AgentConnectionMode.api

    private struct PendingRequest: Sendable {
        let model: String
        let apiKey: String
        let baseURL: URL
        let input: AgentMessageInput
        let previousResponseID: String?
    }

    private var pending: [String: PendingRequest] = [:]
    private var activeTasks: [String: Task<Void, Never>] = [:]
    private var statuses: [String: AgentRunStatus] = [:]
    private var apiKey: String?
    private var baseURL = URL(string: "https://api.openai.com/v1")!
    private var model = ""
    private var agentID: UUID?
    private var latestUsage: AgentUsageSnapshot?

    func connect(configuration: AgentAdapterConfiguration) async throws -> AgentConnectionState {
        guard configuration.providerID == providerID, configuration.mode == .api else {
            throw AgentAdapterError.invalidConfiguration("OpenAI adapter requires provider=openai and mode=api")
        }
        guard let model = configuration.model?.trimmingCharacters(in: .whitespacesAndNewlines),
              !model.isEmpty else {
            throw AgentAdapterError.invalidConfiguration("OpenAI model is required")
        }
        guard let credential = configuration.credential,
              !credential.keychainService.isEmpty,
              !credential.account.isEmpty else {
            throw AgentAdapterError.invalidConfiguration("OpenAI API key must be referenced from Keychain")
        }
        let key = try Self.readKeychain(service: credential.keychainService, account: credential.account)
        let endpoint = configuration.endpoint ?? "https://api.openai.com/v1"
        guard let url = URL(string: endpoint),
              let host = url.host?.lowercased(),
              url.user == nil, url.password == nil,
              url.scheme?.lowercased() == "https"
                || (url.scheme?.lowercased() == "http" && ["localhost", "127.0.0.1", "::1"].contains(host)) else {
            throw AgentAdapterError.invalidConfiguration("OpenAI endpoint must use HTTPS or loopback HTTP and contain no credentials")
        }
        self.apiKey = key
        self.baseURL = url
        self.model = model
        self.agentID = configuration.agentID
        return AgentConnectionState(status: .configured, checkedAt: Date(), message: "Keychain credential loaded; live API request not yet verified")
    }

    func probeCapabilities() async throws -> Set<AgentCapability> {
        guard apiKey != nil else { throw AgentAdapterError.authenticationRequired }
        return [.textInput, .streamingOutput, .sessionResume, .runStatus, .usage]
    }

    func listSessions() async throws -> [AgentSession] {
        throw AgentAdapterError.unsupportedCapability(.sessionListing)
    }

    func sendMessage(sessionID: String?, input: AgentMessageInput) async throws -> AgentRun {
        guard let apiKey else { throw AgentAdapterError.authenticationRequired }
        guard input.attachments.isEmpty else {
            throw AgentAdapterError.unsupportedCapability(.attachments)
        }
        guard input.contextReferenceIDs.isEmpty else {
            throw AgentAdapterError.invalidConfiguration("Context references must be resolved by Pack before sending")
        }
        guard !input.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw AgentAdapterError.invalidConfiguration("Message text is empty")
        }
        guard let agentID else { throw AgentAdapterError.authenticationRequired }
        let run = AgentRun(id: UUID().uuidString, agentID: agentID, sessionID: sessionID, createdAt: Date())
        pending[run.id] = PendingRequest(
            model: model, apiKey: apiKey, baseURL: baseURL,
            input: input, previousResponseID: sessionID
        )
        statuses[run.id] = .queued
        return run
    }

    func streamEvents(for run: AgentRun) async -> AsyncThrowingStream<AgentEvent, Error> {
        guard let request = pending.removeValue(forKey: run.id) else {
            return AsyncThrowingStream { continuation in
                continuation.finish(throwing: AgentAdapterError.runNotFound)
            }
        }
        statuses[run.id] = .running
        return AsyncThrowingStream { continuation in
            let task = Task {
                await self.performStream(run: run, request: request, continuation: continuation)
            }
            self.activeTasks[run.id] = task
            continuation.onTermination = { termination in
                if case .cancelled = termination {
                    Task { await self.cancelLocalStream(runID: run.id) }
                }
            }
        }
    }

    func cancel(run: AgentRun) async throws -> AgentCancelResult {
        guard let task = activeTasks[run.id] else {
            if statuses[run.id] == .completed || statuses[run.id] == .failed {
                return .alreadyFinished
            }
            return .unsupported
        }
        task.cancel()
        return .unconfirmed
    }

    func runStatus(for run: AgentRun) async throws -> AgentRunStatus {
        statuses[run.id] ?? .unknown
    }

    func usageSnapshot() async throws -> AgentUsageSnapshot? {
        latestUsage
    }

    func disconnect() async {
        for task in activeTasks.values { task.cancel() }
        activeTasks.removeAll()
        pending.removeAll()
        apiKey = nil
        agentID = nil
    }

    private func performStream(
        run: AgentRun,
        request: PendingRequest,
        continuation: AsyncThrowingStream<AgentEvent, Error>.Continuation
    ) async {
        do {
            let url = request.baseURL.appendingPathComponent("responses")
            var urlRequest = URLRequest(url: url)
            urlRequest.httpMethod = "POST"
            urlRequest.timeoutInterval = 300
            urlRequest.setValue("Bearer \(request.apiKey)", forHTTPHeaderField: "Authorization")
            urlRequest.setValue("application/json", forHTTPHeaderField: "Content-Type")
            urlRequest.setValue("text/event-stream", forHTTPHeaderField: "Accept")

            var body: [String: Any] = [
                "model": request.model,
                "stream": true,
                "input": [[
                    "role": "user",
                    "content": [[
                        "type": "input_text",
                        "text": request.input.text
                    ]]
                ]]
            ]
            if let previous = request.previousResponseID, !previous.isEmpty {
                body["previous_response_id"] = previous
            }
            urlRequest.httpBody = try JSONSerialization.data(withJSONObject: body)

            let (bytes, response) = try await URLSession.shared.bytes(for: urlRequest)
            guard let http = response as? HTTPURLResponse else {
                throw AgentAdapterError.connectionFailed("OpenAI returned a non-HTTP response")
            }
            guard (200..<300).contains(http.statusCode) else {
                throw AgentAdapterError.providerRejected("OpenAI HTTP \(http.statusCode)")
            }

            for try await line in bytes.lines {
                try Task.checkCancellation()
                guard line.hasPrefix("data:") else { continue }
                let payload = line.dropFirst(5).trimmingCharacters(in: .whitespaces)
                guard payload != "[DONE]", let data = payload.data(using: .utf8),
                      let event = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                      let type = event["type"] as? String else { continue }

                switch type {
                case "response.created":
                    let responseObject = event["response"] as? [String: Any]
                    let responseID = responseObject?["id"] as? String
                    continuation.yield(AgentEvent(
                        kind: .statusChanged, text: nil, status: .running,
                        detail: responseID.map { "response_id=\($0)" },
                        usageInputTokens: nil, usageOutputTokens: nil
                    ))
                case "response.output_text.delta":
                    if let delta = event["delta"] as? String, !delta.isEmpty {
                        continuation.yield(AgentEvent(
                            kind: .messageDelta, text: delta, status: nil, detail: nil,
                            usageInputTokens: nil, usageOutputTokens: nil
                        ))
                    }
                case "response.completed":
                    let responseObject = event["response"] as? [String: Any] ?? [:]
                    let responseID = responseObject["id"] as? String
                    let usage = responseObject["usage"] as? [String: Any] ?? [:]
                    let inputTokens = usage["input_tokens"] as? Int
                    let outputTokens = usage["output_tokens"] as? Int
                    if inputTokens != nil || outputTokens != nil {
                        latestUsage = AgentUsageSnapshot(
                            inputTokens: inputTokens, outputTokens: outputTokens,
                            costAmount: nil, currencyCode: nil, measuredAt: Date()
                        )
                    }
                    statuses[run.id] = .completed
                    continuation.yield(AgentEvent(
                        kind: .messageCompleted, text: nil, status: .completed,
                        detail: responseID.map { "session_id=\($0)" },
                        usageInputTokens: inputTokens, usageOutputTokens: outputTokens
                    ))
                    continuation.finish()
                    activeTasks.removeValue(forKey: run.id)
                    return
                case "response.failed", "error":
                    let message = event["message"] as? String
                        ?? ((event["response"] as? [String: Any])?["error"] as? [String: Any])?["message"] as? String
                        ?? "OpenAI response failed"
                    throw AgentAdapterError.providerRejected(message)
                case "response.incomplete":
                    throw AgentAdapterError.providerRejected("OpenAI response incomplete")
                default:
                    continue
                }
            }
            if statuses[run.id] != .completed {
                throw AgentAdapterError.connectionFailed("OpenAI stream ended before response.completed")
            }
        } catch is CancellationError {
            statuses[run.id] = .cancelled
            continuation.yield(AgentEvent(
                kind: .statusChanged, text: nil, status: .cancelled,
                detail: "Local stream stopped; provider-side cancellation is unconfirmed",
                usageInputTokens: nil, usageOutputTokens: nil
            ))
            continuation.finish()
        } catch {
            statuses[run.id] = .failed
            continuation.finish(throwing: error)
        }
        activeTasks.removeValue(forKey: run.id)
    }

    private func cancelLocalStream(runID: String) {
        activeTasks[runID]?.cancel()
    }

    private static func readKeychain(service: String, account: String) throws -> String {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne
        ]
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        guard status == errSecSuccess,
              let data = result as? Data,
              let value = String(data: data, encoding: .utf8),
              !value.isEmpty else {
            throw AgentAdapterError.authenticationRequired
        }
        return value
    }
}
