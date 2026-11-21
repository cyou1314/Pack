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

    /// Rebuilds in-memory adapters from persisted profiles on app launch.
    /// Missing Keychain entries remain visible as authentication-required profiles.
    func restoreAdapters() async throws -> [AgentProfile] {
        var restored: [AgentProfile] = []
        for var profile in try await store.allProfiles() {
            guard profile.providerID == "openai", profile.mode == .api else { continue }
            guard let credential = profile.credential else {
                profile.connection = AgentConnectionState(
                    status: .authenticationRequired, checkedAt: Date(),
                    message: "Keychain credential reference is missing"
                )
                profile.updatedAt = Date()
                try await store.save(profile)
                restored.append(profile)
                continue
            }
            let adapter = OpenAIResponsesAdapter()
            do {
                let configuration = AgentAdapterConfiguration(
                    agentID: profile.id, providerID: profile.providerID, mode: profile.mode,
                    credential: credential, endpoint: profile.endpoint, model: profile.model,
                    adapterVersion: profile.adapterVersion
                )
                let connection = try await adapter.connect(configuration: configuration)
                profile.capabilities = try await adapter.probeCapabilities()
                profile.connection = connection
                profile.updatedAt = Date()
                try await registry.register(adapter, for: profile.id)
            } catch {
                await adapter.disconnect()
                profile.connection = AgentConnectionState(
                    status: .authenticationRequired, checkedAt: Date(),
                    message: "Could not restore the Keychain credential or adapter"
                )
                profile.updatedAt = Date()
            }
            try await store.save(profile)
            restored.append(profile)
        }
        return restored
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

    func updateProfile(
        agentID: UUID,
        displayName: String,
        model: String,
        endpoint: String?
    ) async throws -> AgentProfile {
        guard var profile = try await store.profile(id: agentID) else {
            throw AgentAdapterError.invalidConfiguration("Agent profile was not found")
        }
        let cleanName = displayName.trimmingCharacters(in: .whitespacesAndNewlines)
        let cleanModel = model.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cleanName.isEmpty, !cleanModel.isEmpty else {
            throw AgentAdapterError.invalidConfiguration("Agent name and model are required")
        }
        let cleanEndpoint = endpoint?.trimmingCharacters(in: .whitespacesAndNewlines)
        let normalizedEndpoint = (cleanEndpoint?.isEmpty == false) ? cleanEndpoint : nil
        var replacementAdapter: OpenAIResponsesAdapter?
        if profile.providerID == "openai", profile.mode == .api {
            let adapter = OpenAIResponsesAdapter()
            let configuration = AgentAdapterConfiguration(
                agentID: profile.id, providerID: profile.providerID, mode: profile.mode,
                credential: profile.credential, endpoint: normalizedEndpoint, model: cleanModel,
                adapterVersion: profile.adapterVersion
            )
            _ = try await adapter.connect(configuration: configuration)
            profile.capabilities = try await adapter.probeCapabilities()
            replacementAdapter = adapter
        } else {
            throw AgentAdapterError.invalidConfiguration("Editing is not supported for this provider")
        }
        let sessionChanged = profile.model != cleanModel || profile.endpoint != normalizedEndpoint
        profile.displayName = cleanName
        profile.model = cleanModel
        profile.endpoint = normalizedEndpoint
        if sessionChanged { profile.lastSessionID = nil }
        profile.connection = AgentConnectionState(
            status: .configured, checkedAt: Date(),
            message: "Profile updated; live API request not yet re-verified"
        )
        profile.updatedAt = Date()
        do {
            try await store.save(profile)
        } catch {
            await replacementAdapter?.disconnect()
            throw error
        }
        if let replacementAdapter {
            await registry.replace(replacementAdapter, for: agentID)
        }
        return profile
    }

    func updateAPIKey(agentID: UUID, newAPIKey: String) async throws {
        guard !newAPIKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw AgentAdapterError.invalidConfiguration("API Key cannot be empty")
        }
        guard var profile = try await store.profile(id: agentID),
              profile.providerID == "openai",
              profile.mode == .api,
              let credential = profile.credential else {
            throw AgentAdapterError.invalidConfiguration("OpenAI API Agent or Keychain reference was not found")
        }
        let oldSecret = try AgentCredentialVault.read(credential)
        _ = try AgentCredentialVault.store(
            secret: newAPIKey,
            service: credential.keychainService,
            account: credential.account
        )
        let adapter = OpenAIResponsesAdapter()
        do {
            let configuration = AgentAdapterConfiguration(
                agentID: profile.id, providerID: profile.providerID, mode: profile.mode,
                credential: credential, endpoint: profile.endpoint, model: profile.model,
                adapterVersion: profile.adapterVersion
            )
            _ = try await adapter.connect(configuration: configuration)
            profile.capabilities = try await adapter.probeCapabilities()
            profile.connection = AgentConnectionState(
                status: .configured, checkedAt: Date(),
                message: "API Key updated in Keychain; live API request not yet verified"
            )
            profile.updatedAt = Date()
            do {
                try await store.save(profile)
            } catch {
                await adapter.disconnect()
                _ = try? AgentCredentialVault.store(
                    secret: oldSecret, service: credential.keychainService, account: credential.account
                )
                throw error
            }
            await registry.replace(adapter, for: agentID)
        } catch {
            await adapter.disconnect()
            _ = try? AgentCredentialVault.store(
                secret: oldSecret, service: credential.keychainService, account: credential.account
            )
            throw error
        }
    }

    func clearChatMessages(agentID: UUID) async throws {
        try await store.clearChatMessages(agentID: agentID)
    }

    func cancel(run: AgentRun) async throws -> AgentCancelResult {
        guard let adapter = await registry.adapter(for: run.agentID) else {
            throw AgentAdapterError.invalidConfiguration("Agent adapter is not registered")
        }
        return try await adapter.cancel(run: run)
    }

    func appendChatMessage(agentID: UUID, role: String, content: String) async throws {
        try await store.appendChatMessage(agentID: agentID, role: role, content: content)
    }

    func chatMessages(agentID: UUID) async throws -> [AgentChatMessage] {
        try await store.chatMessages(agentID: agentID)
    }

    func updateSessionID(agentID: UUID, sessionID: String) async throws {
        guard var profile = try await store.profile(id: agentID) else { return }
        profile.lastSessionID = sessionID
        profile.updatedAt = Date()
        try await store.save(profile)
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
