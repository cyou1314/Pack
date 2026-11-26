import SwiftUI
import PackCore

let executorEnrollment = AgentExecutorEnrollment()

struct AgentBindingSheet: View {
    @Environment(\.dismiss) private var dismiss
    @ObservedObject var state: AppState
    let account: AgentAccountSummary
    let scope: String
    @State private var instance: AgentHubInstance?
    @State private var binding: AgentExecutorBindingStatus?
    @State private var verified = false
    @State private var busy = false
    @State private var note = ""
    @State private var confirmBind = false
    @State private var confirmRevoke = false
    @State private var showManual = false

    private var available: Bool { state.collaborationOwnerAvailable && scope == state.collaborationScope }
    private var destination: AgentExecutionDestination {
        .init(accountRef:account.id.uuidString.lowercased(),provider:account.profile.providerID,
              accessKind:account.profile.mode == .officialAPI ? "api" : "native")
    }
    var body: some View {
        VStack(alignment:.leading,spacing:DesignTokens.settingsGap) {
            Text("Hub 专用执行身份").font(DesignTokens.shellTitle)
            Text(verbatim:account.profile.displayName+" · "+account.profile.providerID)
            Text(verbatim:state.collaborationHubAddress).font(DesignTokens.fontSmall).textSelection(.enabled)
            if let instance {
                Text("实例："+instance.instanceId).font(DesignTokens.fontSmall).textSelection(.enabled)
            }
            if let binding {
                Text(statusLabel(binding)).font(DesignTokens.fontTitle)
                if let member = binding.memberId {
                    Text("成员 #\(member)").font(DesignTokens.fontSmall)
                }
            } else { Text("绑定状态尚未确认").foregroundStyle(DesignTokens.subText) }
            Text("绑定只验证 Hub 专用身份，不验证平台登录、API 可用性或模型能力；不会发送模型请求、上报心跳或启动执行器。")
                .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
            if verified, let instance, let member = binding?.memberId,
               account.profile.mode == .officialAPI && account.credentialState == .apiKeyPresentUnverified {
                Button("手动 API 执行…") { showManual = true }.disabled(busy || !available)
                    .sheet(isPresented:$showManual) {
                        AgentManualAPIView(state:state,account:account,scope:scope,instance:instance,memberId:member)
                    }
            }
            if !note.isEmpty { Text(note).foregroundStyle(DesignTokens.amber) }
            if !available { Text("中枢或房间已变化，请关闭后重新打开。").foregroundStyle(DesignTokens.amber) }
            HStack {
                if busy { ProgressView().controlSize(.small) }
                Button("关闭") { dismiss() }.disabled(busy)
                Spacer()
                Button("查询 / 认证") { Task { await refresh() } }.disabled(busy || !available)
                if binding?.state == "unbound" {
                    Button("确认绑定…") { confirmBind = true }.buttonStyle(.borderedProminent).disabled(busy || !available)
                }
                if binding?.state == "bound" || binding?.state == "unavailable" {
                    Button("撤销身份…") { confirmRevoke = true }.disabled(busy || !available)
                }
            }
        }.font(DesignTokens.fontUI).padding(DesignTokens.settingsInset)
        .frame(width:DesignTokens.settingsWidth)
        .interactiveDismissDisabled(busy)
        .task { await refresh() }
        .alert("绑定此 Agent 到当前 Hub？",isPresented:$confirmBind) {
            Button("确认绑定") { Task { await bind() } }
            Button("取消",role:.cancel) {}
        } message: {
            Text("将为当前数据库与本地档案创建独立执行身份。本机保存专用令牌，Hub 只接收哈希。")
        }
        .confirmationDialog("永久撤销专用执行身份？",isPresented:$confirmRevoke,titleVisibility:.visible) {
            Button("撤销并停止相关协作",role:.destructive) { Task { await revoke() } }
            Button("取消",role:.cancel) {}
        } message: {
            Text("此身份不可重新启用，相关协作停止；已发送的模型请求可能仍执行或计费。本机凭证与账号档案不会删除。")
        }
    }
    private func statusLabel(_ value:AgentExecutorBindingStatus) -> String {
        switch value.state {
        case "unbound": return "尚未绑定"
        case "bound": return verified ? "Hub 身份已认证 · Provider 未验证" : "Hub 已登记 · 本机身份未认证"
        case "revoked": return "已撤销"
        default: return "身份不可用"
        }
    }
    @MainActor private func refresh() async {
        guard !busy,available else { return }
        busy = true; verified = false; binding = nil; note = ""
        defer { busy = false }
        do {
            let transport = try state.executorEnrollmentTransport(scope:scope)
            let current = try await transport.instance()
            // Never silently adopt a replacement DB while this sheet is open.
            guard available,!Task.isCancelled,instance == nil || instance == current else {
                note = "Hub 实例变化，请关闭后重新确认。"; return
            }
            instance = current
            let value = try await transport.status(current,destination)
            guard available,!Task.isCancelled else { return }
            binding = value
            if value.state == "bound",let member = value.memberId {
                let ok = try await executorEnrollment.verifyExisting(instance:current,destination:destination,memberId:member,
                    verify:{ try await transport.verify($0,$1,$2,$3) })
                guard available,!Task.isCancelled else { return }
                verified = ok
                if !ok { note = "专用令牌认证未通过。" }
            }
        } catch {
            note = binding?.state == "bound" ? "Hub 已登记，但本机令牌不可用或认证失败；不会生成替代令牌。" :
                "无法确认 Hub 绑定状态，请检查服务及群主凭证。"
        }
    }
    @MainActor private func bind() async {
        guard !busy,available,let instance,binding?.state == "unbound" else { return }
        busy = true; verified = false; note = ""
        defer { busy = false }
        do {
            let transport = try state.executorEnrollmentTransport(scope:scope)
            let dest = destination
            guard try await transport.instance() == instance,available,!Task.isCancelled else {
                throw AgentEnrollmentError.invalidIdentity
            }
            _ = try await executorEnrollment.enroll(instance:instance,destination:dest,
                provision:{ try await transport.provision($0) },
                verify:{ try await transport.verify($0,$1,$2,$3) })
            guard available,!Task.isCancelled else { return }
            busy = false
            await refresh()
        } catch {
            binding = nil
            note = "绑定结果未确认。请查询当前状态；若尚未登记，可使用原本机令牌再次绑定。"
        }
    }
    @MainActor private func revoke() async {
        guard !busy,available,let instance,let value = binding,let member = value.memberId,
              ["bound","unavailable"].contains(value.state) else { return }
        busy = true; verified = false; note = ""
        defer { busy = false }
        do {
            let transport = try state.executorEnrollmentTransport(scope:scope)
            try await transport.revoke(instance,destination,member)
            guard available,!Task.isCancelled else { return }
            busy = false
            await refresh()
        } catch {
            binding = nil
            note = "撤销结果未确认。请先查询当前状态，不会自动重试。"
        }
    }
}
