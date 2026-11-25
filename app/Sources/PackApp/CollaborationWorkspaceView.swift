import SwiftUI
import PackCore

/// Explicit, on-demand owner workspace. No background provider execution and
/// no fallback to dispatcher credentials. Private result text is in view state
/// only, not UserDefaults or the legacy room message cache.
struct CollaborationWorkspaceView: View {
    @ObservedObject var state: AppState
    @State private var runs: [CollaborationWorkspaceRun] = []
    @State private var cursor: Int?
    @State private var busy = false
    @State private var note = ""
    @State private var result: CollaborationWorkspaceResult?
    @State private var usage: CollaborationWorkspaceUsage?
    @State private var confirmPublish = false
    @State private var loaded = false
    @State private var requestGeneration = UUID()

    var body: some View {
        VStack(alignment: .leading, spacing: DesignTokens.boardGap) {
            HStack {
                VStack(alignment: .leading, spacing: DesignTokens.detailSpacing) {
                    Text("协作工作台").font(DesignTokens.shellTitle)
                    Text("当前房间：\(state.currentRoom) · 成果默认私有").font(DesignTokens.fontSmall)
                        .foregroundStyle(DesignTokens.subText)
                }
                Spacer()
                Button { Task { await load() } } label: { Label("刷新", systemImage: "arrow.clockwise") }
                    .disabled(busy || !state.collaborationOwnerAvailable)
            }
            if !state.collaborationOwnerAvailable {
                Text("需要已连接的中枢及群主凭证。").foregroundStyle(DesignTokens.subText)
            } else {
                if busy { ProgressView().controlSize(.small) }
                if !note.isEmpty { Text(note).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.amber) }
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: DesignTokens.boardGap) {
                        if loaded && runs.isEmpty { Text("当前房间尚无协作任务").foregroundStyle(DesignTokens.subText) }
                        ForEach(runs) { run in runCard(run) }
                        if let cursor {
                            Button("加载更早的协作任务") { Task { await load(before: cursor) } }.disabled(busy)
                        }
                        if let usage { usageCard(usage) }
                        if let result { resultCard(result) }
                    }
                }
            }
        }
        .font(DesignTokens.fontUI)
        .padding(DesignTokens.boardInset)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .task { await load() }
        .onChange(of: state.collaborationOwnerAvailable) { _, available in
            clear()
            if available { Task { await load() } }
        }
        .onDisappear { clear() }
        .confirmationDialog("发布已审核最终成果？", isPresented: $confirmPublish, titleVisibility: .visible) {
            Button("确认发布到当前房间") { if let result { Task { await publish(result) } } }
            Button("取消", role: .cancel) {}
        } message: {
            Text("房间读者将看到完整正文；已读取或转发的内容无法撤回。")
        }
    }

    private func runCard(_ run: CollaborationWorkspaceRun) -> some View {
        VStack(alignment: .leading, spacing: DesignTokens.boardCardGap) {
            HStack {
                Text(modeLabel(run.mode)).font(DesignTokens.fontTitle)
                Spacer()
                Text(statusLabel(run.status)).foregroundStyle(run.status == "succeeded" ? DesignTokens.okGreen : DesignTokens.subText)
            }
            Text("已认领 \(run.callsUsed) / \(run.maxCalls) · \(run.steps.count) 个步骤")
                .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
            ProgressView(value: Double(run.steps.filter { $0.status == "succeeded" }.count),
                         total: Double(max(run.steps.count,1)))
            ForEach(run.steps) { step in
                HStack {
                    Text(roleLabel(step.role))
                    Text("成员 #\(step.member)").foregroundStyle(DesignTokens.subText)
                    Spacer()
                    Text(statusLabel(step.taskStatus ?? step.status)).font(DesignTokens.fontSmall)
                    if step.hasResult {
                        Button("查看") { Task { await readResult(run.id, step: step.key) } }.disabled(busy)
                    }
                }
            }
            Divider()
            HStack {
                Button("用量") { Task { await readUsage(run.id) } }.disabled(busy)
                Spacer()
                if run.publication != nil { Text("已发布").foregroundStyle(DesignTokens.okGreen) }
                if run.finalResultAvailable {
                    Button("查看最终成果") { Task { await readResult(run.id) } }.disabled(busy)
                }
            }
        }
        .padding(DesignTokens.boardCardInset)
        .background(DesignTokens.shellSurface, in: RoundedRectangle(cornerRadius: DesignTokens.boardCardRadius))
        .overlay(RoundedRectangle(cornerRadius: DesignTokens.boardCardRadius).stroke(DesignTokens.hairline))
    }
    private func usageCard(_ value: CollaborationWorkspaceUsage) -> some View {
        VStack(alignment: .leading, spacing: DesignTokens.detailSpacing) {
            Text("执行用量 · \(value.runId.prefix(8))").font(DesignTokens.fontTitle)
            Text("执行登记 \(value.registeredAttempts) 次 · 已报告 Token \(value.meteredAttempts) 次 · 未计量 \(value.unmeteredRegisteredAttempts) 次")
            Text("输入：\(value.reportedInputTokens.map(String.init) ?? "未知") · 输出：\(value.reportedOutputTokens.map(String.init) ?? "未知")")
            Text("执行体上报数据，非平台结算账单。").font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
            Button("关闭") { usage = nil }
        }.padding(DesignTokens.detailPadding)
    }
    private func resultCard(_ value: CollaborationWorkspaceResult) -> some View {
        VStack(alignment: .leading, spacing: DesignTokens.detailSpacing) {
            HStack {
                Text(value.stepKey == nil ? "最终成果" : "步骤成果 · \(value.stepKey ?? "")").font(DesignTokens.fontTitle)
                Spacer()
                Text(value.approved ? "已审核" : "待审核").foregroundStyle(DesignTokens.subText)
                Button("关闭") { result = nil; confirmPublish = false }
            }
            // Verbatim plain text; no model-generated clickable links or markup.
            Text(verbatim: value.result).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
            if value.canPublishFinal && runs.first(where: { $0.id == value.runId })?.publication == nil {
                Button("发布最终成果…") { confirmPublish = true }.disabled(busy)
            }
        }.padding(DesignTokens.detailPadding)
        .background(DesignTokens.shellSurface, in: RoundedRectangle(cornerRadius: DesignTokens.radiusCard))
    }
    private func clear() {
        requestGeneration = UUID()
        runs = []; cursor = nil; result = nil; usage = nil; note = ""; loaded = false
        busy = false; confirmPublish = false
    }
    @MainActor private func load(before: Int? = nil) async {
        guard !busy, state.collaborationOwnerAvailable else { return }
        let generation = requestGeneration, scope = state.collaborationScope, room = state.currentRoom
        busy = true; note = ""
        if before == nil { result = nil; usage = nil }
        defer { if generation == requestGeneration { busy = false } }
        do {
            let page = try await state.collaborationOwnerRequest("collaboration_workspace",
                args: ["room":room,"before_source_id":before ?? 0,"limit":20],
                scope: scope, as: CollaborationWorkspacePage.self)
            guard generation == requestGeneration, !Task.isCancelled, page.room == room else { return }
            if before == nil { runs = page.runs } else {
                let ids = Set(runs.map(\.id)); runs += page.runs.filter { !ids.contains($0.id) }
            }
            cursor = page.nextBeforeSourceId; loaded = true
        } catch {
            guard generation == requestGeneration, !Task.isCancelled else { return }
            if before == nil { runs = []; cursor = nil; loaded = false }
            note = "无法读取协作工作台。当前 Hub 可能尚未启用协作功能，或群主权限不可用。"
        }
    }
    @MainActor private func readResult(_ id: String, step: String = "") async {
        guard !busy, state.collaborationOwnerAvailable else { return }
        let generation = requestGeneration, scope = state.collaborationScope
        busy = true; note = ""; result = nil
        defer { if generation == requestGeneration { busy = false } }
        do {
            let value = try await state.collaborationOwnerRequest("collaboration_result_get",
                args: ["run_id":id,"step_key":step], scope: scope, as: CollaborationWorkspaceResult.self)
            guard generation == requestGeneration, !Task.isCancelled, value.runId == id,
                  value.stepKey == (step.isEmpty ? nil : step) else { return }
            result = value
        } catch { if generation == requestGeneration { note = "成果暂不可读取，请刷新后重试。" } }
    }
    @MainActor private func readUsage(_ id: String) async {
        guard !busy, state.collaborationOwnerAvailable else { return }
        let generation = requestGeneration, scope = state.collaborationScope
        busy = true; note = ""; usage = nil
        defer { if generation == requestGeneration { busy = false } }
        do {
            let value = try await state.collaborationOwnerRequest("collaboration_usage_get",
                args: ["run_id":id], scope: scope, as: CollaborationWorkspaceUsage.self)
            guard generation == requestGeneration, !Task.isCancelled, value.runId == id else { return }
            usage = value
        } catch { if generation == requestGeneration { note = "用量暂不可读取。" } }
    }
    @MainActor private func publish(_ value: CollaborationWorkspaceResult) async {
        guard !busy, state.collaborationOwnerAvailable, value.canPublishFinal,
              let digest = value.sha256, runs.contains(where: { $0.id == value.runId && $0.publication == nil })
        else { return }
        let generation = requestGeneration, scope = state.collaborationScope
        busy = true; note = ""
        do {
            let receipt = try await state.collaborationOwnerRequest("collaboration_publish",
                args: ["run_id":value.runId,"expected_sha256":digest], scope: scope, as: CollaborationPublishReceipt.self)
            guard generation == requestGeneration, !Task.isCancelled else { return }
            guard receipt.runId == value.runId, receipt.published, receipt.messageId > 0 else {
                throw HubError.badResponse("publication receipt unavailable")
            }
            busy = false
            await load()
            if generation == requestGeneration { note = "最终成果已发布。" }
        } catch {
            if generation == requestGeneration {
                note = "发布结果未确认，请先刷新核对；不会自动重新发布。"
                result = nil
                busy = false
            }
        }
    }
    private func modeLabel(_ mode: String) -> String {
        ["focus":"单 Agent","compare":"独立分析","team":"团队协作"][mode] ?? mode
    }
    private func roleLabel(_ role: String) -> String {
        ["answer":"回答","independent-analysis":"分析","worker":"执行","review":"审核","synthesis":"汇总"][role] ?? role
    }
    private func statusLabel(_ status: String) -> String {
        ["active":"进行中","pending":"等待依赖","open":"待认领","claimed":"已认领","working":"执行中",
         "running":"执行中","review":"待审核","done":"已审核","succeeded":"已完成","failed":"失败",
         "cancelled":"已取消","expired":"已过期"][status] ?? status
    }
}
