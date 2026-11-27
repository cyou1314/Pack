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
    @State private var showCreate = false
    private enum Control {
        case review(run: String, step: String, version: String, approve: Bool)
        case cancel(run: String)
        var prompt: String {
            switch self {
            case .review(_,_,_,true): return "通过审核后，已授权的后续步骤可能被派发。"
            case .review(_,_,_,false): return "拒绝成果会停止整组协作，不会自动重试。"
            case .cancel: return "停止整组协作；已经发送给模型的请求可能仍在运行或计费。"
            }
        }
        var destructive: Bool {
            switch self { case .review(_,_,_,true): return false; default: return true }
        }
    }
    @State private var control: Control?
    @State private var confirmControl = false
    @State private var uncertainRun: String?
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
                Button { showCreate = true } label: { Label("新建协作", systemImage: "plus") }
                    .disabled(busy || !loaded || !state.collaborationOwnerAvailable)
                Button { Task { await load() } } label: { Label("刷新", systemImage: "arrow.clockwise") }
                    .disabled(busy || !state.collaborationOwnerAvailable)
            }
            if !state.collaborationOwnerAvailable {
                Text("需要已连接的中枢及群主凭证。").foregroundStyle(DesignTokens.subText)
            } else {
                if busy { ProgressView().controlSize(.small) }
                if !note.isEmpty { Text(note).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.amber) }
                if let uncertainRun {
                    Button("核对操作状态 · \(uncertainRun.prefix(8))") {
                        Task { await reconcile(uncertainRun) }
                    }.disabled(busy)
                }
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
        .sheet(isPresented: $showCreate, onDismiss: { Task { await load() } }) {
            CollaborationCreateView(state: state, scope: state.collaborationScope)
        }
        .task { await load() }
        .onChange(of: state.collaborationOwnerAvailable) { _, available in
            clear()
            if available { Task { await load() } }
        }
        .onDisappear { clear() }
        .alert("确认协作操作", isPresented: $confirmControl, presenting: control) { action in
            Button(action.destructive ? "确认停止" : "确认通过", role: action.destructive ? .destructive : nil) {
                Task { await applyControl(action) }
            }
            Button("返回", role: .cancel) { control = nil }
        } message: { action in
            Text(action.prompt)
        }
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
            if let budgets = run.contextBudgets, !budgets.isEmpty {
                ForEach(budgets) { budget in budgetRow(budget) }
            }
            Divider()
            HStack {
                Button("用量") { Task { await readUsage(run.id) } }.disabled(busy)
                if run.status == "active" {
                    Button("取消协作…") { control = .cancel(run: run.id); confirmControl = true }
                        .disabled(busy || uncertainRun != nil)
                }
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
    private func budgetRow(_ value: CollaborationContextBudget) -> some View {
        VStack(alignment:.leading,spacing:DesignTokens.detailSpacing) {
            Text("后续 \(value.stepKey) · 上下文至少 \(value.minimumBytes) / \(value.maxBytes) 字节")
                .foregroundStyle(value.overBudget ? DesignTokens.amber : DesignTokens.subText)
            if value.missingResults > 0 || value.unapprovedResults > 0 {
                Text("尚缺 \(value.missingResults) 份成果 · \(value.unapprovedResults) 份待审核；实际输入可能变化")
                    .foregroundStyle(DesignTokens.subText)
            }
            Text("均分建议：每份依赖成果 JSON 文本净增约 \(value.suggestedEncodedTextBytes) 字节；非 Token 限额")
                .foregroundStyle(DesignTokens.subText)
            if value.overBudget {
                Text("当前投影已超预算。若成果保持不变，后续派发可能失败；原文不会自动截断。")
                    .foregroundStyle(DesignTokens.amber)
            }
        }.font(DesignTokens.fontSmall)
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
            if let budgets = value.contextBudgets, !budgets.isEmpty {
                ForEach(budgets) { budget in budgetRow(budget) }
                if budgets.contains(where:{ $0.overBudget }) {
                    Text("通过审核仍会保留原始成果；若下一步无法容纳上下文，协作可能停止。")
                        .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.amber)
                }
            }
            if value.canReview, let step = value.stepKey, let version = value.reviewVersion,
               runs.contains(where: { $0.id == value.runId && $0.status == "active" &&
                   $0.steps.contains { $0.key == step && $0.taskStatus == "review" } }) {
                HStack {
                    Button("通过审核…") {
                        control = .review(run:value.runId,step:step,version:version,approve:true)
                        confirmControl = true
                    }.buttonStyle(.borderedProminent)
                    Button("拒绝并停止…") {
                        control = .review(run:value.runId,step:step,version:version,approve:false)
                        confirmControl = true
                    }
                }.disabled(busy || uncertainRun != nil)
            } else if !value.approved {
                Text("该成果当前不能在此审核。结构化成果请使用原任务板。")
                    .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
            }
            if value.canPublishFinal && runs.first(where: { $0.id == value.runId })?.publication == nil {
                Button("发布最终成果…") { confirmPublish = true }.disabled(busy)
            }
        }.padding(DesignTokens.detailPadding)
        .background(DesignTokens.shellSurface, in: RoundedRectangle(cornerRadius: DesignTokens.radiusCard))
    }
    private func clear() {
        requestGeneration = UUID()
        runs = []; cursor = nil; result = nil; usage = nil; note = ""; loaded = false
        busy = false; confirmPublish = false; confirmControl = false; control = nil; uncertainRun = nil
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
    @MainActor private func applyControl(_ action: Control) async {
        guard !busy, uncertainRun == nil, state.collaborationOwnerAvailable else { return }
        let generation = requestGeneration, scope = state.collaborationScope
        let runId: String
        switch action {
        case .review(let run,let step,let version,_):
            guard result?.runId == run, result?.stepKey == step, result?.reviewVersion == version,
                  result?.canReview == true,
                  runs.contains(where: { $0.id == run && $0.status == "active" &&
                      $0.steps.contains { $0.key == step && $0.taskStatus == "review" } }) else { return }
            runId = run
        case .cancel(let run):
            guard runs.contains(where: { $0.id == run && $0.status == "active" }) else { return }
            runId = run
        }
        busy = true; note = ""; control = nil
        defer { if generation == requestGeneration { busy = false } }
        do {
            let status: String
            switch action {
            case .review(let run,let step,let version,let approve):
                let receipt = try await state.collaborationOwnerRequest("collaboration_review",
                    args:["run_id":run,"step_key":step,"expected_version":version,"approve":approve],
                    scope:scope, as:CollaborationReviewReceipt.self)
                guard receipt.runId == run, receipt.stepKey == step,
                      receipt.status == (approve ? "done" : "cancelled") else {
                    throw HubError.badResponse("review receipt mismatch")
                }
                status = receipt.runStatus
            case .cancel(let run):
                let receipt = try await state.collaborationOwnerRequest("collaboration_cancel",
                    args:["run_id":run],scope:scope,as:CollaborationControlStatus.self)
                guard receipt.runId == run, ["cancelled","failed","expired","succeeded"].contains(receipt.status)
                else { throw HubError.badResponse("cancel receipt mismatch") }
                status = receipt.status
            }
            guard generation == requestGeneration, !Task.isCancelled else { return }
            result = nil; busy = false
            await load()
            if generation == requestGeneration { note = "操作已确认 · \(statusLabel(status))" }
        } catch {
            guard generation == requestGeneration else { return }
            result = nil; uncertainRun = runId
            note = "操作结果未确认，请核对当前状态；不会自动重复审核或取消。"
        }
    }

    @MainActor private func reconcile(_ id: String) async {
        guard !busy, uncertainRun == id, state.collaborationOwnerAvailable else { return }
        let generation = requestGeneration, scope = state.collaborationScope
        busy = true
        defer { if generation == requestGeneration { busy = false } }
        do {
            let value = try await state.collaborationOwnerRequest("collaboration_status",
                args:["run_id":id],scope:scope,as:CollaborationControlStatus.self)
            guard generation == requestGeneration, value.runId == id, !Task.isCancelled else { return }
            busy = false
            await load()
            if generation == requestGeneration && loaded {
                uncertainRun = nil
                note = "已读取当前协作状态 · \(statusLabel(value.status))；请重新查看步骤，不代表原操作必然成功。"
            }
        } catch { if generation == requestGeneration { note = "状态核对失败，尚未确认原操作结果。" } }
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
