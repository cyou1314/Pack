import SwiftUI
import PackCore

// Device-local profiles are not Hub members. A single process-wide manager
// owns metadata and credential setup; never reuse legacy member tokens.
let localAgentAccounts = AgentAccountManager(
    directory: FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent(".pack",isDirectory:true)
        .appendingPathComponent("agents-v2",isDirectory:true))

struct AgentAccountsView: View {
    @ObservedObject var state: AppState
    @State private var bindingTarget: AgentAccountSummary?
    @State private var nativeLoginTarget: AgentAccountSummary?
    @State private var accounts: [AgentAccountSummary] = []
    @State private var note = ""
    @State private var loaded = false
    @State private var busy = false
    @State private var adding = false
    @State private var keyTarget: AgentAccountSummary?
    @State private var generation = UUID()

    var body: some View {
        VStack(alignment:.leading,spacing:DesignTokens.boardGap) {
            HStack {
                VStack(alignment:.leading,spacing:DesignTokens.detailSpacing) {
                    Text("Agent 账号").font(DesignTokens.shellTitle)
                    Text("本机档案 · 原生个人账号 / 官方 API").foregroundStyle(DesignTokens.subText)
                }
                Spacer()
                Button { adding = true } label: { Label("添加 Agent",systemImage:"plus") }.disabled(busy || !loaded)
                Button { Task { await refresh() } } label: { Label("刷新",systemImage:"arrow.clockwise") }.disabled(busy)
            }
            Text("本地档案不等于已上线成员。登录、API 联通性及 Hub 执行绑定需另行验证。")
                .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
            if busy { ProgressView().controlSize(.small) }
            if !note.isEmpty { Text(note).foregroundStyle(DesignTokens.amber) }
            ScrollView {
                LazyVStack(alignment:.leading,spacing:DesignTokens.boardGap) {
                    if loaded && accounts.isEmpty { Text("尚无本地 Agent 档案").foregroundStyle(DesignTokens.subText) }
                    ForEach(accounts) { account in accountCard(account) }
                }
            }
        }.font(DesignTokens.fontUI).padding(DesignTokens.boardInset)
        .frame(maxWidth:.infinity,maxHeight:.infinity,alignment:.topLeading)
        .task { await refresh() }
        .onDisappear { generation = UUID(); accounts = []; loaded = false; busy = false }
        .sheet(isPresented:$adding,onDismiss:{ Task { await refresh() } }) { AgentAccountCreateSheet() }
        .sheet(item:$nativeLoginTarget) { account in
            NativeLoginGuide(account:account)
        }
        .sheet(item:$bindingTarget) { account in
            AgentBindingSheet(state:state,account:account,scope:state.collaborationScope)
        }
        .sheet(item:$keyTarget,onDismiss:{ Task { await refresh() } }) { account in
            AgentAccountKeySheet(account:account)
        }
    }

    private func accountCard(_ account:AgentAccountSummary) -> some View {
        let profile = account.profile
        return VStack(alignment:.leading,spacing:DesignTokens.detailSpacing) {
            HStack {
                Text(verbatim:profile.displayName).font(DesignTokens.fontTitle)
                Spacer()
                Text(profile.mode == .nativeAccount ? "原生账号" : "官方 API")
            }
            Text(verbatim:AgentProviderCatalog.candidates.first(where:{$0.id == profile.providerID})?.displayName ?? profile.providerID)
                .foregroundStyle(DesignTokens.subText)
            if let model = profile.modelID { Text(verbatim:"模型："+model) }
            Text(profile.mode == .nativeAccount ? "平台管理上下文及记忆；实际登录/记忆状态未验证。" :
                "Pack 组装任务上下文；持久记忆暂关闭。")
                .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
            Text(statusLabel(account.credentialState)).foregroundStyle(DesignTokens.amber)
            Text("档案 ID："+profile.id.uuidString.lowercased()).font(DesignTokens.fontSmall).textSelection(.enabled)
            if account.credentialState == .apiKeyMissing {
                Button("配置 API Key…") { keyTarget = account }.disabled(busy)
            }
            if let monitor = state.manualPresence[account.id] {
                Text(monitor.label).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
                if monitor.running {
                    Text("待执行任务：\(monitor.tasks.count)").font(DesignTokens.fontSmall)
                    Button("停止接单") { state.stopManualPresence(account.id) }
                }
            }
            if profile.mode == .nativeAccount && ["chatgpt","gemini","doubao"].contains(profile.providerID) {
                Button("原生登录（开发版）…") { nativeLoginTarget = account }.disabled(busy)
            }
            if profile.providerID != "muse" {
                Button("Hub 身份绑定…") { bindingTarget = account }
                    .disabled(busy || !state.collaborationOwnerAvailable)
            }
        }.padding(DesignTokens.boardCardInset)
        .frame(maxWidth:.infinity,alignment:.leading)
        .background(DesignTokens.shellSurface,in:RoundedRectangle(cornerRadius:DesignTokens.boardCardRadius))
        .overlay(RoundedRectangle(cornerRadius:DesignTokens.boardCardRadius).stroke(DesignTokens.hairline))
    }

    private func statusLabel(_ value:AgentLocalCredentialState) -> String {
        switch value {
        case .nativeSessionUnverified: return "待原生登录及会话验证"
        case .apiKeyMissing: return "尚未配置 API Key"
        case .apiKeyPresentUnverified: return "Keychain 已保存 · 尚未验证 API"
        case .apiKeyUnavailable: return "凭证读取不可用 · 不会自动覆盖"
        }
    }

    @MainActor private func refresh() async {
        guard !busy else { return }
        let current = generation
        busy = true; note = ""
        defer { if current == generation { busy = false } }
        do {
            let rows = try await localAgentAccounts.list()
            guard !Task.isCancelled,current == generation else { return }
            accounts = rows; loaded = true
        } catch {
            guard current == generation else { return }
            accounts = []; loaded = false
            note = "本地账号注册表不可用。请检查 ~/.pack 私有目录、权限或其他 Pack 实例。未覆盖原数据。"
        }
    }
}

private struct AgentAccountCreateSheet: View {
    @Environment(\.dismiss) private var dismiss
    @State private var mode: AgentConnectionMode = .nativeAccount
    @State private var providerID = "chatgpt"
    @State private var name = ""
    @State private var model = ""
    @State private var busy = false
    @State private var note = ""

    private var candidates: [AgentProvider] {
        AgentProviderCatalog.candidates.filter { $0.modes.contains(mode) && $0.id != "muse" }
    }
    var body: some View {
        VStack(alignment:.leading,spacing:DesignTokens.settingsGap) {
            Text("添加 Agent 档案").font(DesignTokens.shellTitle)
            Picker("接入方式",selection:$mode) {
                Text("原生个人账号").tag(AgentConnectionMode.nativeAccount)
                Text("官方 API").tag(AgentConnectionMode.officialAPI)
            }.pickerStyle(.segmented)
            Picker("平台",selection:$providerID) {
                ForEach(candidates,id:\.id) { provider in Text(provider.displayName).tag(provider.id) }
            }
            TextField("档案显示名称",text:$name)
            if mode == .officialAPI {
                TextField("官方模型 ID（必填）",text:$model)
                Text("先创建档案，再独立配置 Keychain API Key；不会读取个人账号记忆。")
                    .foregroundStyle(DesignTokens.subText)
            } else {
                Text("此步仅创建独立会话标识，实际平台登录、记忆及工具仍待验证。")
                    .foregroundStyle(DesignTokens.subText)
            }
            Text("当前仅登记文本能力，不会发起模型请求或创建 Hub 成员。")
                .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
            if !note.isEmpty { Text(note).foregroundStyle(DesignTokens.amber) }
            HStack {
                if busy { ProgressView().controlSize(.small) }
                Spacer()
                Button("取消") { dismiss() }.disabled(busy)
                Button("创建档案") { Task { await create() } }.buttonStyle(.borderedProminent)
                    .disabled(busy || name.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty ||
                              (mode == .officialAPI && model.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty))
            }
        }.font(DesignTokens.fontUI).padding(DesignTokens.settingsInset)
        .frame(width:DesignTokens.settingsWidth)
        .interactiveDismissDisabled(busy)
        .onChange(of:mode) { _,_ in providerID = candidates.first?.id ?? ""; model = ""; note = "" }
        .disabled(busy)
    }

    @MainActor private func create() async {
        guard !busy else { return }
        busy = true; note = ""
        defer { busy = false }
        do {
            _ = try await localAgentAccounts.create(providerID:providerID,mode:mode,
                displayName:name,modelID:mode == .officialAPI ? model : nil)
            dismiss()
        } catch { note = "无法创建档案。请检查显示名称、模型 ID 或本地存储权限。" }
    }
}

private struct AgentAccountKeySheet: View {
    @Environment(\.dismiss) private var dismiss
    let account:AgentAccountSummary
    @State private var key = ""
    @State private var busy = false
    @State private var note = ""
    var body: some View {
        VStack(alignment:.leading,spacing:DesignTokens.settingsGap) {
            Text("配置 API Key").font(DesignTokens.shellTitle)
            Text(verbatim:account.profile.displayName+" · "+account.profile.providerID)
            SecureField("API Key",text:$key)
            Text("仅写入本机 device-only Keychain；不发送给 Hub、不写入档案 JSON，不验证实际 API 调用。已有凭证不会覆盖。")
                .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
            if !note.isEmpty { Text(note).foregroundStyle(DesignTokens.amber) }
            HStack {
                if busy { ProgressView().controlSize(.small) }
                Spacer()
                Button("取消") { key = ""; dismiss() }.disabled(busy)
                Button("保存密钥") { Task { await save() } }.buttonStyle(.borderedProminent)
                    .disabled(busy || key.isEmpty || key.utf8.count > 8192)
            }
        }.font(DesignTokens.fontUI).padding(DesignTokens.settingsInset)
        .frame(width:DesignTokens.settingsWidth)
        .disabled(busy).interactiveDismissDisabled(busy)
        .onDisappear { key = "" }
    }

    @MainActor private func save() async {
        guard !busy else { return }
        let value = key
        key = ""; busy = true; note = ""
        defer { busy = false }
        do {
            try await localAgentAccounts.configureMissingAPIKey(id:account.id,key:value)
            dismiss()
        } catch { note = "保存结果未确认或凭证已存在。请关闭并刷新核对；不会自动覆盖。" }
    }
}


private struct NativeLoginGuide: View {
    @Environment(\.dismiss) private var dismiss
    let account:AgentAccountSummary
    private var command:String {
        "node runtime/browser-driver/native-login-cli.mjs " + account.id.uuidString.lowercased()
    }
    var body: some View {
        VStack(alignment:.leading,spacing:DesignTokens.settingsGap) {
            Text("原生账号登录 · 开发版").font(DesignTokens.shellTitle)
            Text(verbatim:account.profile.displayName)
            Text("当前正式安装包尚未内置经过完整性验证的浏览器运行时。仅在可信 Pack 源码及已安装匹配 Chromium 的开发环境中使用以下命令。")
                .foregroundStyle(DesignTokens.subText)
            Text("在仓库根目录的终端执行：").font(DesignTokens.fontSmall)
            Text(verbatim:command).font(.system(.body,design:.monospaced)).textSelection(.enabled)
            Text("只读页面诊断：在同一命令末尾添加 --diagnose。回车时仅输出固定控件数量，不包含聊天内容或账号信息。")
                .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
            Text("浏览器会打开独立档案。请自行登录平台；终端回车关闭并保留会话。不验证登录身份、模型或记忆，不启动执行器。")
                .foregroundStyle(DesignTokens.subText)
            HStack {
                Spacer()
                Button("关闭") { dismiss() }
            }
        }.font(DesignTokens.fontUI).padding(DesignTokens.settingsInset)
        .frame(width:DesignTokens.settingsWidth)
    }
}
