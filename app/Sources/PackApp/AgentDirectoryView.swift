import SwiftUI

@MainActor
final class AgentWorkspaceState: ObservableObject {
    @Published var profiles: [AgentProfile] = []
    @Published var selectedAgentID: UUID?
    @Published var displayName = ""
    @Published var model = ""
    @Published var apiKey = ""
    @Published var endpoint = ""
    @Published var chatInput = ""
    @Published var chatOutput = ""
    @Published var isAdding = false
    @Published var isTesting = false
    @Published var isSending = false
    @Published var note = ""
    @Published var error = ""

    private let manager: AgentManager?

    init() {
        do {
            manager = AgentManager(store: try AgentProfileStore())
            Task { await bootstrap() }
        } catch {
            manager = nil
            self.error = "Agent 本地数据库初始化失败：\(error.localizedDescription)"
        }
    }

    var selectedProfile: AgentProfile? {
        profiles.first { $0.id == selectedAgentID }
    }

    func bootstrap() async {
        guard let manager else { return }
        do {
            _ = try await manager.restoreAdapters()
            profiles = try await manager.profiles()
            if selectedAgentID == nil { selectedAgentID = profiles.first?.id }
            note = "Agent 配置已从本机数据库加载"
        } catch {
            self.error = error.localizedDescription
        }
    }

    func addOpenAI() {
        guard let manager else { error = "Agent 管理器不可用"; return }
        guard !displayName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              !model.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              !apiKey.isEmpty else {
            error = "请填写名称、模型 ID 和 API Key"
            return
        }
        isAdding = true
        error = ""
        Task {
            defer { isAdding = false }
            do {
                let profile = try await manager.addOpenAIProfile(
                    displayName: displayName, model: model, apiKey: apiKey,
                    endpoint: endpoint.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : endpoint
                )
                profiles = try await manager.profiles()
                selectedAgentID = profile.id
                displayName = ""
                model = ""
                apiKey = ""
                endpoint = ""
                note = "Agent 已保存；连接状态为待验证"
            } catch {
                self.error = error.localizedDescription
            }
        }
    }

    func testSelectedConnection() {
        guard let manager, let selectedAgentID else { return }
        isTesting = true
        error = ""
        Task {
            defer { isTesting = false }
            do {
                let response = try await manager.testConnection(agentID: selectedAgentID)
                chatOutput += "\n[连接测试]\n" + response + "\n"
                profiles = try await manager.profiles()
                note = "API 连接测试完成"
            } catch {
                self.error = error.localizedDescription
                profiles = (try? await manager.profiles()) ?? profiles
            }
        }
    }

    func sendMessage() {
        guard let manager, let profile = selectedProfile else { return }
        let text = chatInput.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        chatInput = ""
        isSending = true
        error = ""
        chatOutput += "\n你：\n\(text)\n"
        Task {
            defer { isSending = false }
            do {
                let run = try await manager.sendMessage(agentID: profile.id, text: text)
                let stream = try await manager.streamEvents(for: run)
                var responseID: String?
                for try await event in stream {
                    if event.kind == .messageDelta {
                        chatOutput += event.text ?? ""
                    }
                    if let detail = event.detail, detail.hasPrefix("session_id=") {
                        responseID = String(detail.dropFirst("session_id=".count))
                    }
                }
                if let responseID {
                    note = "回复完成；会话续接 ID 已就绪"
                } else {
                    note = "回复完成"
                }
            } catch {
                self.error = error.localizedDescription
            }
        }
    }

    func removeSelected() {
        guard let manager, let selectedAgentID else { return }
        Task {
            do {
                _ = try await manager.removeProfile(agentID: selectedAgentID)
                profiles = try await manager.profiles()
                self.selectedAgentID = profiles.first?.id
                chatOutput = ""
                note = "Agent 配置已移除"
            } catch {
                self.error = error.localizedDescription
            }
        }
    }
}

struct AgentDirectoryView: View {
    @ObservedObject var state: AgentWorkspaceState
    @State private var showingAdd = false
    @State private var showRemoveConfirmation = false

    var body: some View {
        HStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text("Agent").font(.system(size: 18, weight: .semibold))
                    Spacer()
                    Button { showingAdd = true } label: {
                        Label("添加", systemImage: "plus")
                    }.buttonStyle(.borderedProminent)
                }
                ForEach(state.profiles) { profile in
                    Button { state.selectedAgentID = profile.id } label: {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(profile.displayName).font(.system(size: 13, weight: .medium))
                            Text("\(profile.providerID.uppercased()) · \(profile.model ?? "未设置模型")")
                                .font(.system(size: 10)).foregroundStyle(.secondary)
                            Text(profile.connection?.status.rawValue ?? "disconnected")
                                .font(.system(size: 10)).foregroundStyle(.secondary)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(10)
                        .background(state.selectedAgentID == profile.id ? Color.accentColor.opacity(0.12) : Color.clear, in: RoundedRectangle(cornerRadius: 8))
                    }.buttonStyle(.plain)
                }
                Spacer()
                Text("API 密钥仅保存在 macOS Keychain").font(.system(size: 10)).foregroundStyle(.secondary)
            }
            .padding(16)
            .frame(width: 240)
            Divider()
            detail
        }
        .sheet(isPresented: $showingAdd) {
            addSheet
        }
        .confirmationDialog("移除 Agent？", isPresented: $showRemoveConfirmation, titleVisibility: .visible) {
            Button("移除并删除 Keychain 凭证", role: .destructive) { state.removeSelected() }
            Button("取消", role: .cancel) {}
        } message: {
            Text("这会删除本机 Agent 配置和对应 API Key，不会影响服务商账号。")
        }
    }

    @ViewBuilder
    private var detail: some View {
        if let profile = state.selectedProfile {
            VStack(alignment: .leading, spacing: 14) {
                HStack {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(profile.displayName).font(.system(size: 20, weight: .semibold))
                        Text("\(profile.providerID) · 官方 API · \(profile.model ?? "")")
                            .font(.system(size: 11)).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button("测试连接") { state.testSelectedConnection() }
                        .disabled(state.isTesting)
                    Button(role: .destructive) { showRemoveConfirmation = true } label: {
                        Image(systemName: "trash")
                    }
                }
                HStack(spacing: 8) {
                    Text("状态：\(profile.connection?.status.rawValue ?? "disconnected")")
                    if state.isTesting { ProgressView().controlSize(.small) }
                    Spacer()
                    Text(profile.connection?.message ?? "尚未测试").foregroundStyle(.secondary)
                }.font(.system(size: 11))
                Divider()
                ScrollView {
                    Text(state.chatOutput.isEmpty ? "发送一条消息，验证 Agent API 适配器。" : state.chatOutput)
                        .font(.system(size: 13, design: .monospaced))
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .textSelection(.enabled)
                }
                HStack {
                    TextField("发送消息…", text: $state.chatInput, axis: .vertical)
                        .textFieldStyle(.roundedBorder)
                        .lineLimit(1...4)
                    Button("发送") { state.sendMessage() }
                        .buttonStyle(.borderedProminent)
                        .disabled(state.isSending || state.chatInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
                if state.isSending { ProgressView("正在生成…").controlSize(.small) }
                if !state.note.isEmpty { Text(state.note).font(.system(size: 11)).foregroundStyle(.secondary) }
                if !state.error.isEmpty { Text(state.error).font(.system(size: 11)).foregroundStyle(.red) }
            }
            .padding(20)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        } else {
            VStack(spacing: 10) {
                Image(systemName: "cpu").font(.system(size: 32)).foregroundStyle(.secondary)
                Text("还没有 Agent").font(.system(size: 16, weight: .medium))
                Text("添加一个官方 API Agent，或等待后续 Native Adapter 接入。")
                    .font(.system(size: 12)).foregroundStyle(.secondary)
                Button("添加 OpenAI API Agent") { showingAdd = true }.buttonStyle(.borderedProminent)
                if !state.error.isEmpty { Text(state.error).foregroundStyle(.red) }
            }.frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    private var addSheet: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("添加 OpenAI API Agent").font(.system(size: 18, weight: .semibold))
            Text("使用 OpenAI API Key。此模式独立于 ChatGPT 个人账号和订阅。")
                .font(.system(size: 11)).foregroundStyle(.secondary)
            TextField("显示名称", text: $state.displayName).textFieldStyle(.roundedBorder)
            TextField("模型 ID", text: $state.model).textFieldStyle(.roundedBorder)
            SecureField("OpenAI API Key", text: $state.apiKey).textFieldStyle(.roundedBorder)
            TextField("自定义 API Endpoint（可选）", text: $state.endpoint).textFieldStyle(.roundedBorder)
            if !state.error.isEmpty { Text(state.error).font(.system(size: 11)).foregroundStyle(.red) }
            HStack {
                Spacer()
                Button("取消") { showingAdd = false }
                Button("保存到本机") {
                    state.addOpenAI()
                    if state.error.isEmpty { showingAdd = false }
                }
                .buttonStyle(.borderedProminent)
                .disabled(state.isAdding)
            }
        }
        .padding(20)
        .frame(width: 420)
    }
}
