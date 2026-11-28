import SwiftUI
import PackCore

struct CollaborationCreateView: View {
    @ObservedObject var state: AppState
    let scope: String
    @Environment(\.dismiss) private var dismiss
    @State private var bodyText = ""
    @State private var options = CollaborationRouteOptions()
    @State private var minutes = 30
    @State private var preview: CollaborationRoutePreview?
    @State private var previewOptions: CollaborationRouteOptions?
    @State private var pending: CollaborationCreateRequest?
    @State private var busy = false
    @State private var note = ""
    @State private var consent = false
    @State private var abandon = false

    private var available: Bool { state.collaborationOwnerAvailable && state.collaborationScope == scope }
    private var ready: Bool {
        available && !busy && pending == nil && consent &&
        previewOptions == options && preview?.accepts(options) == true &&
        !bodyText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && bodyText.utf8.count <= 12*1024
    }

    var body: some View {
        VStack(alignment: .leading, spacing: DesignTokens.boardGap) {
            Text("新建协作任务").font(DesignTokens.shellTitle)
            Text("房间：\(state.currentRoom) · 私有任务").foregroundStyle(DesignTokens.subText)
            ScrollView {
                VStack(alignment: .leading, spacing: DesignTokens.boardGap) {
                    fields.disabled(busy || pending != nil || !available)
                    if let preview { routeCard(preview) }
                    if pending == nil {
                        Toggle("确认上述成员可读取任务及其直接依赖成果；模型发送仍须单独授权。", isOn: $consent)
                            .disabled(busy || preview?.accepts(options) != true)
                    }
                    if let pending {
                        Text("提交核对键：\(pending.requestKey)").textSelection(.enabled).font(DesignTokens.fontSmall)
                        Text("原参数已锁定。请先核对；若重新提交，只会使用同一请求键。")
                            .foregroundStyle(DesignTokens.amber)
                    }
                    if !note.isEmpty { Text(note).foregroundStyle(DesignTokens.amber) }
                    if !available { Text("当前房间或中枢已切换、断开，操作不可用。").foregroundStyle(DesignTokens.amber) }
                }
            }
            HStack {
                if busy { ProgressView().controlSize(.small) }
                Spacer()
                Button(pending == nil ? "取消" : "放弃本次核对…") {
                    if pending == nil { dismiss() } else { abandon = true }
                }.disabled(busy)
                if let pending {
                    Button("核对提交") { Task { await lookup(pending) } }.disabled(busy || !available)
                    Button("使用原请求重试") { Task { await submit(pending) } }
                        .disabled(busy || !available || pending.deadline <= Date().timeIntervalSince1970)
                } else {
                    Button("确认创建") { beginSubmit() }.buttonStyle(.borderedProminent).disabled(!ready)
                }
            }
        }
        .font(DesignTokens.fontUI)
        .padding(DesignTokens.settingsInset)
        .frame(width: DesignTokens.settingsWidth, height: DesignTokens.memberLoginSheetHeight)
        .interactiveDismissDisabled(busy || pending != nil)
        .onChange(of: options) { _, _ in preview = nil; previewOptions = nil; consent = false }
        .onChange(of: bodyText) { _, _ in consent = false }
        .onChange(of: minutes) { _, _ in consent = false }
        .onChange(of: state.collaborationScope) { _, new in if new != scope { dismiss() } }
        .confirmationDialog("放弃本次提交核对？", isPresented: $abandon) {
            Button("放弃核对", role: .destructive) { dismiss() }
            Button("返回", role: .cancel) {}
        } message: {
            Text("任务可能已创建。请先在工作台刷新确认，不要重复新建相同任务。")
        }
    }

    private var fields: some View {
        VStack(alignment: .leading, spacing: DesignTokens.boardGap) {
            Text("任务正文").font(DesignTokens.fontTitle)
            TextEditor(text: $bodyText).font(DesignTokens.fontUI)
                .frame(minHeight: DesignTokens.memberLoginHeight / 3)
                .border(DesignTokens.hairline)
            Text("\(bodyText.utf8.count) / 12288 UTF-8 字节 · 不写入群聊正文")
                .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
            Picker("协作模式", selection: $options.mode) {
                Text("Focus · 单 Agent").tag(CollaborationMode.focus)
                Text("Compare · 独立分析").tag(CollaborationMode.compare)
                Text("Team · 团队协作").tag(CollaborationMode.team)
            }
            .onChange(of: options.mode) { _, mode in
                options.workers = max(1,min(options.workers,mode.maxWorkers))
                options.maxCalls = max(options.maxCalls,options.plannedCalls)
            }
            if options.mode != .focus {
                Stepper(options.mode == .compare ? "额外独立分析成员：\(options.workers)" : "并行执行成员：\(options.workers)",
                        value: $options.workers, in: 1...max(1,options.mode.maxWorkers))
            }
            Stepper("调用预算：\(options.maxCalls) 次（计划 \(options.plannedCalls) 次）", value: $options.maxCalls, in: 1...8)
            if options.valid {
                Text("首批 \(options.firstWaveCalls) 个任务 · \(options.sequentialWaves) 个依赖阶段（非耗时估计）")
                    .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
                if options.maxCalls > options.plannedCalls {
                    Button("预算设为计划所需次数") { options.maxCalls = options.plannedCalls }
                        .font(DesignTokens.fontSmall)
                }
            }
            Picker("截止时间（提交后）", selection: $minutes) {
                Text("15 分钟").tag(15); Text("30 分钟").tag(30)
                Text("1 小时").tag(60); Text("4 小时").tag(240); Text("24 小时").tag(1440)
            }
            Toggle("允许额度未知的候选账号", isOn: $options.allowUnknownQuota)
            Button("获取角色分配建议") { Task { await loadPreview() } }
                .disabled(!options.valid)
            Text("按接单状态、任务占用、执行权限与额度筛选；多 Agent 优先选择不同平台家族。不代表已登录或模型能力验证。")
                .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
        }
    }

    private func routeCard(_ value: CollaborationRoutePreview) -> some View {
        VStack(alignment: .leading, spacing: DesignTokens.detailSpacing) {
            Text("角色分配预览").font(DesignTokens.fontTitle)
            if let route = value.suggestion, value.accepts(options) {
                Text("\(options.mode == .focus ? "回答" : options.mode == .compare ? "独立分析 + 汇总" : "主导 / 汇总")：\(memberLabel(route.leader, value))")
                ForEach(route.workers, id: \.self) { member in
                    Text("\(options.mode == .compare ? "独立分析" : "执行")：\(memberLabel(member, value))")
                }
                if let reviewer = route.reviewer { Text("审核：\(memberLabel(reviewer, value))") }
                Text("计划 \(route.plannedCalls) 次模型执行；不包括 Provider 内部调用。")
                    .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
            } else {
                Text("没有足够符合条件的账号。可调整模式、成员数或额度选项后重新获取。")
                    .foregroundStyle(DesignTokens.amber)
            }
            ForEach(value.candidates) { candidate in
                HStack {
                    Text(candidate.memberName ?? "成员 #\(candidate.id)")
                    Text(candidate.provider).foregroundStyle(DesignTokens.subText)
                    Spacer()
                    Text(candidate.eligible(allowUnknown: options.allowUnknownQuota) ? "候选" :
                         candidate.executionAllowed == false ? "执行未获准" :
                         !candidate.online ? "离线" : candidate.activePlan || candidate.queue > 0 ? "占用" :
                         candidate.quotaState == "unknown" ? "额度未知" : "额度受限")
                        .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
                }
            }
        }.padding(DesignTokens.boardCardInset)
            .background(DesignTokens.shellSurface, in: RoundedRectangle(cornerRadius: DesignTokens.radiusCard))
    }
    private func memberLabel(_ id: Int, _ preview: CollaborationRoutePreview) -> String {
        guard let c = preview.candidates.first(where: { $0.id == id }) else { return "成员 #\(id)" }
        return "\(c.memberName ?? "成员 #\(id)") · \(c.provider) · \(c.accessKind == "api" ? "API" : "原生账号")"
    }
    @MainActor private func loadPreview() async {
        guard available, !busy, pending == nil, options.valid else { return }
        let snapshot = options
        busy = true; note = ""; preview = nil; previewOptions = nil; consent = false
        defer { busy = false }
        do {
            let value = try await state.collaborationOwnerRequest("collaboration_route_preview",
                args: ["mode":snapshot.mode.rawValue,"worker_count":snapshot.workers,
                       "max_calls":snapshot.maxCalls,"allow_unknown_quota":snapshot.allowUnknownQuota],
                scope: scope, as: CollaborationRoutePreview.self)
            guard available, options == snapshot, !Task.isCancelled else { return }
            preview = value; previewOptions = snapshot
        } catch { note = "无法获取分配建议，请检查协作服务与群主权限。" }
    }
    @MainActor private func beginSubmit() {
        guard ready, let preview, let request = CollaborationCreateRequest(room: state.currentRoom,
              body: bodyText, options: options, preview: preview, durationMinutes: minutes) else { return }
        pending = request
        Task { await submit(request) }
    }
    @MainActor private func submit(_ request: CollaborationCreateRequest) async {
        guard available, !busy, pending?.requestKey == request.requestKey else { return }
        busy = true; note = ""
        defer { busy = false }
        var args: [String:Any] = ["room":request.room,"request_key":request.requestKey,"body":request.body,
                                  "mode":request.options.mode.rawValue,"leader":request.route.leader,
                                  "workers":request.route.workers,"max_calls":request.options.maxCalls,"deadline":request.deadline]
        if let reviewer = request.route.reviewer { args["reviewer"] = reviewer }
        do {
            let receipt = try await state.collaborationOwnerRequest("collaboration_create",
                args: args, scope: scope, as: CollaborationCreateReceipt.self)
            guard available, receipt.matches(request), !Task.isCancelled else {
                note = "提交回执未确认，请核对。"; return
            }
            pending = nil
            dismiss()
        } catch { note = "创建结果未确认。请核对提交，不会自动重试。" }
    }
    @MainActor private func lookup(_ request: CollaborationCreateRequest) async {
        guard available, !busy, pending?.requestKey == request.requestKey else { return }
        busy = true; note = ""
        defer { busy = false }
        do {
            let result = try await state.collaborationOwnerRequest("collaboration_request_lookup",
                args: ["room":request.room,"request_key":request.requestKey], scope: scope, as: CollaborationRequestLookup.self)
            guard available, result.room == request.room, result.requestKey == request.requestKey else {
                note = "核对回执不匹配。"; return
            }
            if let id = result.runId, !id.isEmpty { pending = nil; dismiss() }
            else { note = "目前未查到该请求。仍可能在处理中；如重试，请使用原请求。" }
        } catch { note = "核对失败，请保持原请求并稍后手动核对。" }
    }
}
