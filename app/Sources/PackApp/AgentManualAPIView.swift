import SwiftUI
import AppKit
import PackCore
import CryptoKit

struct ManualTask: Decodable, Identifiable, Sendable {
    let id: Int
    let room: String
    let status: String
    let collaborationContextRequired: Bool
    enum CodingKeys: String, CodingKey {
        case id,room,status
        case collaborationContextRequired = "collaboration_context_required"
    }
}
struct ManualTasks: Decodable, Sendable { let tasks: [ManualTask] }

struct AgentManualAPIView: View {
    @Environment(\.dismiss) private var dismiss
    @ObservedObject var state: AppState
    let account: AgentAccountSummary
    let scope: String
    let instance: AgentHubInstance
    let memberId: Int
    @State private var tasks: [ManualTask] = []
    @State private var selected: Int?
    @State private var busy = false
    @State private var note = ""
    @State private var confirmReady = false
    @State private var confirmExecute = false
    @State private var uncertain = false

    private var available: Bool { state.collaborationOwnerAvailable && scope == state.collaborationScope }
    private var destination: AgentExecutionDestination {
        .init(accountRef:account.id.uuidString.lowercased(),provider:account.profile.providerID,accessKind:"api")
    }
    var body: some View {
        VStack(alignment:.leading,spacing:DesignTokens.boardGap) {
            Text("手动 API 执行").font(DesignTokens.shellTitle)
            Text(verbatim:account.profile.displayName+" · "+(account.profile.modelID ?? ""))
            Text("当前房间：\(state.currentRoom) · 成员 #\(memberId)").foregroundStyle(DesignTokens.subText)
            Text("启动接单后可关闭窗口并创建任务；监听器持续刷新队列。执行仍需选中任务并确认实际正文。")
                .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
            HStack {
                if let monitor = state.manualPresence[account.id] {
                    Text(monitor.label).font(DesignTokens.fontSmall)
                    if monitor.running {
                        Button("停止接单") { state.stopManualPresence(account.id) }.disabled(busy)
                    }
                }
                Button("启动接单…") { confirmReady = true }
                    .disabled(busy || !available || uncertain || state.manualPresenceJobs[account.id] != nil)
                Button("刷新待执行任务") { Task { await refresh() } }.disabled(busy || !available)
                if busy { ProgressView().controlSize(.small) }
            }
            ScrollView {
                VStack(alignment:.leading,spacing:DesignTokens.detailSpacing) {
                    if tasks.isEmpty { Text("当前没有可认领的协作任务").foregroundStyle(DesignTokens.subText) }
                    ForEach(tasks) { task in
                        Button {
                            selected = task.id
                        } label: {
                            HStack {
                                Image(systemName:selected == task.id ? "largecircle.fill.circle" : "circle")
                                Text("协作任务 #\(task.id)")
                                Spacer()
                                Text("待认领").foregroundStyle(DesignTokens.subText)
                            }.contentShape(Rectangle())
                        }.buttonStyle(.plain).disabled(busy || uncertain)
                    }
                }
            }.frame(minHeight:120)
            if !note.isEmpty { Text(note).foregroundStyle(DesignTokens.amber) }
            if uncertain {
                Text("本次结果不明确。请在协作工作台核对任务和用量；如存在保留租约，不应手动删除租约后盲目重试。")
                    .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.amber)
            }
            HStack {
                Spacer()
                Button("关闭") { dismiss() }.disabled(busy)
                Button("执行选中任务…") { confirmExecute = true }.buttonStyle(.borderedProminent)
                    .disabled(busy || !available || uncertain || selected == nil)
            }
        }.font(DesignTokens.fontUI).padding(DesignTokens.settingsInset)
        .frame(width:DesignTokens.settingsWidth)
        .interactiveDismissDisabled(busy)
        .task { await refresh() }
        .onChange(of: state.manualPresence[account.id]?.revision) { _, _ in
            guard !busy, let monitor = state.manualPresence[account.id] else { return }
            tasks = monitor.tasks.filter { $0.room == state.currentRoom }
            if let selected, !tasks.contains(where:{$0.id == selected}) { self.selected = nil }
        }
        .alert("启动手动接单监听？",isPresented:$confirmReady) {
            Button("登记") { Task { await announceReady() } }
            Button("取消",role:.cancel) {}
        } message: {
            Text("持续续租并查询待执行任务，离开页面仍接单。停止接单或失联后退出新任务分配。不会自动认领或调用模型。")
        }
        .confirmationDialog("认领并准备执行所选任务？",isPresented:$confirmExecute,titleVisibility:.visible) {
            Button("认领任务") { if let selected { Task { await execute(selected) } } }
            Button("取消",role:.cancel) {}
        } message: {
            Text("认领消耗一次协作调用预算。拒绝出站授权将停止本次协作，不会重新认领。")
        }
    }

    private func withHub<T: Sendable>(_ operation: @escaping @Sendable (InstanceBoundCollaborationHub) async throws -> T) async throws -> T {
        let transport = try state.executorEnrollmentTransport(scope:scope)
        guard try await transport.instance() == instance, available else { throw AgentEnrollmentError.invalidIdentity }
        let dest = destination, id = memberId, pinned = instance, url = transport.url
        let status = try await transport.status(pinned,dest)
        guard status.state == "bound", status.memberId == id, available else { throw AgentEnrollmentError.invalidIdentity }
        return try await executorEnrollment.withExistingToken(instance:pinned,destination:dest) { token in
            let client = try DedicatedCollaborationHub(url:url,memberToken:token)
            return try await operation(.init(client:client,instance:pinned,destination:dest,memberId:id))
        }
    }

    @MainActor private func refresh() async {
        guard !busy,available else { return }
        busy = true; tasks = []; selected = nil
        defer { busy = false }
        do {
            let room = state.currentRoom
            let rows = try await withHub { hub in
                let args = try JSONSerialization.data(withJSONObject:["status":"open","room":room])
                let bytes = try await hub.callOnce(name:"task_list",arguments:args)
                guard bytes.count <= 1_048_576 else { throw AgentAPIError.responseTooLarge }
                return try JSONDecoder().decode(ManualTasks.self,from:bytes).tasks
            }
            guard available,!Task.isCancelled else { return }
            tasks = rows.filter { $0.id > 0 && $0.room == room && $0.status == "open" && $0.collaborationContextRequired }
        } catch { note = "无法读取待执行任务，请检查专用身份或连接。" }
    }

    @MainActor private func announceReady() async {
        guard !busy,available,!uncertain else { return }
        do {
            try state.startManualPresence(account:account,scope:scope,instance:instance,memberId:memberId)
            note = "监听器启动中。可关闭窗口，返回协作工作台创建任务。"
        } catch { note = "无法启动接单监听。" }
    }

    @MainActor private func execute(_ task: Int) async {
        guard !busy,available,!uncertain,tasks.contains(where:{$0.id == task}) else { return }
        busy = true; note = ""; selected = nil
        defer { busy = false }
        // Stop new assignments and wait for the monitor to relinquish its
        // token before manual execution. Existing assigned tasks remain valid.
        await state.stopManualPresenceAndWait(account.id)
        guard available else { return }
        do {
            let dest = destination
            let result = try await withHub { hub in
                let host = try await localAgentAccounts.manualAPIHost(hub:hub,destination:dest)
                return await host.execute(taskID:task,maxOutputTokens:4096,authorize:{ approval in
                    await approve(approval,task:task)
                })
            }
            guard available else { return }
            switch result {
            case .review: note = "已提交成果，等待群主审核。接单已暂停，可手动重新启动。"
            case .blocked(let code,_): note = "协作已停止（\(code)）。"
            case .unavailable: note = "执行不可用：可能存在账号租约、身份或密钥问题。"
            case .claimUnknown,.stopUnknown,.deliveryUnknown:
                uncertain = true; note = "执行结果未确认；不会自动重试。"
            }
            tasks = []
            busy = false
            await refresh()
        } catch {
            uncertain = true; note = "执行状态未确认，请核对协作任务。"
        }
    }

    /// The exact prepared outgoing JSON is reviewed before the adapter loads
    /// its API key. No secret headers or hidden model substitutions.
    @MainActor private func approve(_ approval:AgentAPIAuthorization, task:Int) -> Bool {
        guard available,busy,approval.destination == destination,approval.deadline > Date(),
              approval.body.count <= 256*1024,
              SHA256.hash(data:approval.body).map({String(format:"%02x",$0)}).joined() == approval.bodySHA256,
              let text = String(data:approval.body,encoding:.utf8) else { return false }
        let alert = NSAlert()
        alert.messageText = "允许本次模型请求？"
        alert.informativeText = "任务 #\(task) · \(approval.url.absoluteString)\n可能产生费用。正文如下（不含 API Key）。拒绝将停止本次协作。"
        alert.addButton(withTitle:"发送一次")
        alert.addButton(withTitle:"拒绝")
        let scroll = NSScrollView(frame:NSRect(x:0,y:0,width:560,height:260))
        scroll.hasVerticalScroller = true
        let content = NSTextView(frame:scroll.bounds)
        content.isEditable = false
        content.isSelectable = true
        content.string = text
        content.font = .monospacedSystemFont(ofSize:12,weight:.regular)
        content.isVerticallyResizable = true
        content.autoresizingMask = [.width]
        content.textContainer?.widthTracksTextView = true
        scroll.documentView = content
        alert.accessoryView = scroll
        let choice = alert.runModal()
        return choice == .alertFirstButtonReturn && available && approval.deadline > Date()
    }
}
