import Foundation

/// Coordinates profile persistence, Keychain credentials and adapter lifecycle.
/// UI code should call this service rather than manipulating adapters directly.
actor AgentManager {
    private let store: AgentProfileStore
    private let registry: AgentAdapterRegistry
    private let keychainService: String

    init(
        store: AgentProfileStore,
        registry: AgentAdapterRegistry = AgentAdapterRegistry(),
        keychainService: String = (Bundle.main.bundleIdentifier ?? "com.cyou.pack") + ".agent.openai"
    ) {
        self.store = store
        self.registry = registry
        self.keychainService = keychainService
    }

    func profiles() async throws -> [AgentProfile] {
        try await store.allProfiles()
    }

    /// Adds an OpenAI API profile. The API key is written to Keychain first;
    /// the SQLite profile stores only its Keychain reference.
    func addOpenAIProfile(
        displayName: String,
        model: String,
        apiKey: String,
        endpoint: String? = nil
    ) async throws -> AgentProfile {
        let id = UUID()
        let credential = try AgentCredentialVault.store(
            secret: apiKey,
            service: keychainService,
            account: id.uuidString
        )
        let adapter = OpenAIResponsesAdapter()
        do {
            let configuration = AgentAdapterConfiguration(
                agentID: id,
                providerID: "openai",
                mode: .api,
                credential: credential,
                endpoint: endpoint,
                model: model,
                adapterVersion: "1.0.0"
            )
            let connection = try await adapter.connect(configuration: configuration)
            let capabilities = try await adapter.probeCapabilities()
            let now = Date()
            let profile = AgentProfile(
                id: id,
                displayName: displayName,
                providerID: "openai",
                mode: .api,
                credential: credential,
                endpoint: endpoint,
                model: model,
                adapterVersion: "1.0.0",
                capabilities: capabilities,
                connection: connection,
                createdAt: now,
                updatedAt: now
            )
            try await store.add(profile)
            try await registry.register(adapter, for: id)
            return profile
        } catch {
            await adapter.disconnect()
            try? await store.remove(id: id)
            try? AgentCredentialVault.delete(credential)
            throw error
        }
    }

    func sendMessage(
        agentID: UUID,
        text: String,
        sessionID: String? = nil
    ) async throws -> AgentRun {
        guard let adapter = await registry.adapter(for: agentID) else {
            throw AgentAdapterError.invalidConfiguration("Agent adapter is not connected in this app session")
        }
        return try await adapter.sendMessage(
            sessionID: sessionID,
            input: AgentMessageInput(text: text, attachments: [], contextReferenceIDs: [])
        )
    }

    func streamEvents(for run: AgentRun) async throws -> AsyncThrowingStream<AgentEvent, Error> {
        guard let adapter = await registry.adapter(for: run.agentID) else {
            throw AgentAdapterError.invalidConfiguration("Agent adapter is not registered")
        }
        return await adapter.streamEvents(for: run)
    }

    /// Performs a real, billable minimal request. Call only after explicit user action.
    func testConnection(agentID: UUID) async throws -> String {
        guard let adapter = await registry.adapter(for: agentID) else {
            throw AgentAdapterError.invalidConfiguration("Agent adapter is not registered")
        }
        let run = try await adapter.sendMessage(
            sessionID: nil,
            input: AgentMessageInput(
                text: "Reply with exactly: PACK_CONNECTION_OK",
                attachments: [],
                contextReferenceIDs: []
            )
        )
        let stream = await adapter.streamEvents(for: run)
        var output = ""
        do {
            for try await event in stream {
                if event.kind == .messageDelta { output += event.text ?? "" }
                if event.kind == .messageCompleted {
                    try await updateConnection(
                        agentID: agentID,
                        state: AgentConnectionState(status: .connected, checkedAt: Date(), message: "OpenAI API request verified")
                    )
                }
            }
        } catch {
            try? await updateConnection(
                agentID: agentID,
                state: AgentConnectionState(status: .degraded, checkedAt: Date(), message: "OpenAI API request failed")
            )
            throw error
        }
        return output
    }

    func removeProfile(agentID: UUID) async throws -> Bool {
        guard let profile = try await store.profile(id: agentID) else { return false }
        let secret = try profile.credential.map { try AgentCredentialVault.read($0) }
        if let credential = profile.credential {
            try AgentCredentialVault.delete(credential)
        }
        do {
            _ = try await store.remove(id: agentID)
        } catch {
            if let credential = profile.credential, let secret {
                _ = try? AgentCredentialVault.store(
                    secret: secret,
                    service: credential.keychainService,
                    account: credential.account
                )
            }
            throw error
        }
        await registry.remove(agentID: agentID)
        return true
    }

    private func updateConnection(agentID: UUID, state: AgentConnectionState) async throws {
        guard var profile = try await store.profile(id: agentID) else { return }
        profile.connection = state
        profile.updatedAt = Date()
        try await store.save(profile)
    }
}
