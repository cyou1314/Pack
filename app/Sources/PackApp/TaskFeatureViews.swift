import SwiftUI
import AppKit
import UniformTypeIdentifiers
import WebKit

struct CatalogView: View {
    @ObservedObject var state: AppState
    var body: some View {
        VStack(alignment: .leading, spacing: DesignTokens.settingsGap) {
            Text("选择\(state.catalogKind)").font(DesignTokens.fontTitle)
            if !state.catalogNote.isEmpty { Text(state.catalogNote).font(DesignTokens.fontSmall) }
            if state.catalogKind == "模板" { TextField("模板参数（如需要）", text: $state.catalogArgument).textFieldStyle(.roundedBorder) }
            ScrollView {
                ForEach(Array(state.catalogItems.enumerated()), id: \.offset) { _, item in
                    let name = item["name"] as? String ?? "?"
                    Button(name) {
                        if state.catalogKind == "技能" { state.draftMessage += "【技能：\(name)】"; state.showCatalog = false }
                        else { state.fireTemplate(name) }
                    }.buttonStyle(.plain).frame(maxWidth: .infinity, alignment: .leading).padding(DesignTokens.detailPadding)
                }
            }
            Button("关闭") { state.showCatalog = false }
        }.padding(DesignTokens.settingsInset).frame(width: DesignTokens.memberLoginWidth, height: DesignTokens.memberLoginHeight)
    }
}

struct EmptyRoomView: View {
    @ObservedObject var state: AppState
    var body: some View {
        VStack(spacing: DesignTokens.memberGap) {
            if let url = Bundle.main.url(forResource: "mark", withExtension: "png"), let mark = NSImage(contentsOf: url) {
                Image(nsImage: mark).resizable().scaledToFit().frame(width: DesignTokens.emptyMarkSize, height: DesignTokens.emptyMarkSize)
            }
            Text("新对话").font(DesignTokens.shellTitle)
            Text("在下方输入，或点一个快捷开始").font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
            shortcut("查资料", draft: "让 absent 查个资料：")
            shortcut("生成图", draft: "让 absent 生成一张图：")
            shortcut("巡检额度", draft: "巡检一下各成员额度")
        }.frame(maxWidth: .infinity, minHeight: DesignTokens.emptyRoomHeight)
    }
    private func shortcut(_ title: String, draft: String) -> some View {
        Button { state.draftMessage = draft } label: {
            Text(title).font(DesignTokens.fontUI).frame(maxWidth: DesignTokens.emptyShortcutWidth, minHeight: DesignTokens.emptyShortcutHeight)
                .overlay(RoundedRectangle(cornerRadius: DesignTokens.radiusCard).stroke(DesignTokens.hairline, lineWidth: DesignTokens.boardLine))
        }.buttonStyle(.plain)
    }
}

struct TaskCard: View {
    @ObservedObject var state: AppState
    let task: PackTask
    private var expanded: Bool { state.expandedChatTaskId == task.id }
    var body: some View {
        VStack(alignment: .leading, spacing: DesignTokens.detailSpacing) {
            Button { state.toggleChatTask(task.id) } label: {
                HStack(alignment: .top, spacing: DesignTokens.detailSpacing) {
                    VStack(alignment: .leading, spacing: DesignTokens.detailSpacing) {
                        Text("任务 #\(task.id) · \(task.title)").font(DesignTokens.fontTitle)
                        Text(task.body).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText).lineLimit(expanded ? nil : 2)
                    }
                    Spacer(minLength: 0)
                    Text(task.statusLabel).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
                    Image(systemName: expanded ? "chevron.up" : "chevron.down")
                }.contentShape(Rectangle())
            }.buttonStyle(.plain)
            if expanded { ChatTaskDetail(state: state, task: task).transition(.opacity.combined(with: .move(edge: .top))) }
        }.padding(DesignTokens.detailPadding)
            .background(DesignTokens.shellSurface, in: RoundedRectangle(cornerRadius: DesignTokens.radiusCard))
            .overlay(RoundedRectangle(cornerRadius: DesignTokens.radiusCard).stroke(DesignTokens.hairline, lineWidth: DesignTokens.boardLine))
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct ChatTaskDetail: View {
    @ObservedObject var state: AppState
    let task: PackTask
    private func json(_ value: Any) -> String {
        guard JSONSerialization.isValidJSONObject(value), let data = try? JSONSerialization.data(withJSONObject: value, options: [.prettyPrinted, .sortedKeys]), let text = String(data: data, encoding: .utf8) else { return String(describing: value) }
        return text
    }
    var body: some View {
        VStack(alignment: .leading, spacing: DesignTokens.detailSpacing) {
            Divider()
            Text("执行者：\(task.assignee ?? "未指派") · 尝试次数：\(task.attempts)").font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
            if let lease = task.leaseExpires { Text("租约到期：\(Date(timeIntervalSince1970: lease).formatted())").font(DesignTokens.fontSmall) }
            Text("五步时间线").font(DesignTokens.fontTitle)
            ForEach([("创建", "task_create"), ("认领", "task_claim"), ("执行", "run_start"), ("交活", "task_complete"), ("验收", "task_review")], id: \.1) { label, action in
                let event = state.taskEvents.last { ($0["action"] as? String) == action }
                let stamp = event.flatMap { optDoubleVal($0["ts"]) }
                HStack {
                    Text(label).font(DesignTokens.fontUI)
                    Spacer()
                    if let stamp { Text(Date(timeIntervalSince1970: stamp).formatted()).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText) }
                    else { Text(state.detailLoadNote.isEmpty ? "暂无记录" : "未确认").font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText) }
                }
            }
            if !state.detailLoadNote.isEmpty { Text(state.detailLoadNote).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText) }
            ForEach(Array(state.taskRuns.enumerated()), id: \.offset) { _, run in
                let start = doubleVal(run["started_at"]); let end = optDoubleVal(run["ended_at"])
                Text("\((run["member"] as? String) ?? "成员") · 第 \(intVal(run["fence"])) 次 · \((run["outcome"] as? String) ?? (run["status"] as? String) ?? "未知")" + (end.map { " · \(Int(max(0, $0 - start))) 秒" } ?? " · 进行中"))
                    .font(DesignTokens.fontSmall)
            }
            Text("交活信封").font(DesignTokens.fontTitle)
            if let note = task.reviewNote, !note.isEmpty { Text("验收意见：\(note)").font(DesignTokens.fontSmall).textSelection(.enabled) }
            if let envelope = task.envelope {
                Text("结论：\(envelope.verdict)\n摘要：\(envelope.summary)").font(DesignTokens.fontUI).textSelection(.enabled)
                Text("产物：\(json(envelope.artifacts))").font(DesignTokens.fontSmall).textSelection(.enabled)
                Text("自检：\(envelope.selfCheck ?? "未提供")").font(DesignTokens.fontSmall).textSelection(.enabled)
                if !envelope.knownIssues.isEmpty { Text("已知问题：\(envelope.knownIssues.joined(separator: "；"))").font(DesignTokens.fontSmall) }
                if !envelope.followups.isEmpty { Text("后续事项：\(envelope.followups.joined(separator: "；"))").font(DesignTokens.fontSmall) }
            } else { Text(task.result ?? "暂无交活信封").font(DesignTokens.fontSmall).textSelection(.enabled) }
            Text("转交记录").font(DesignTokens.fontTitle)
            if state.detailLoadNote.isEmpty && state.taskTransfers.isEmpty { Text("暂无转交记录").font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText) }
            ForEach(Array(state.taskTransfers.enumerated()), id: \.offset) { _, transfer in
                Text("\((transfer["from_member"] as? String) ?? "?") → \((transfer["to_member"] as? String) ?? "?") · \((transfer["status"] as? String) ?? "?") · \((transfer["reason"] as? String) ?? "")").font(DesignTokens.fontSmall).textSelection(.enabled)
            }
            Text("审计记录").font(DesignTokens.fontTitle)
            ForEach(Array(state.taskEvents.enumerated()), id: \.offset) { _, event in
                Text("\((event["action"] as? String) ?? "事件") · \((event["actor"] as? String) ?? "") · \((event["detail"] as? String) ?? "")").font(DesignTokens.fontSmall).textSelection(.enabled)
            }
        }.frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct QuestionCard: View {
    @ObservedObject var state: AppState
    let message: RoomMessage
    private var input: Binding<String> {
        Binding(get: { state.questionInputs[message.id] ?? "" }, set: { state.questionInputs[message.id] = $0 })
    }
    private var answer: (author: String, body: String)? { state.questionAnswer(for: message) }
    private var canAnswer: Bool { ["owner", "dispatcher"].contains(state.questionResponderName ?? "") }
    var body: some View {
        VStack(alignment: .leading, spacing: DesignTokens.detailSpacing) {
            Text("提问 · \(message.taskId.map { "任务 #\($0)" } ?? "任务编号未知")")
                .font(DesignTokens.fontTitle).foregroundStyle(DesignTokens.amber)
            Text("提问者：\(message.author)").font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
            Text(message.body).font(DesignTokens.fontUI).textSelection(.enabled)
            if let answer {
                Text("\(answer.author) 已答复：\(answer.body)").font(DesignTokens.fontSmall).textSelection(.enabled)
            } else if state.questionLinksUnavailable {
                Text("答复关联暂不可用，请稍后刷新。").font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
            } else if let payload = message.questionPayload {
                if canAnswer {
                    ForEach(Array(payload.options.enumerated()), id: \.offset) { _, option in
                        Button(option) { state.answerQuestion(message, body: option) }
                            .buttonStyle(.bordered).disabled(state.questionSending.contains(message.id))
                    }
                    if payload.allowInput {
                        HStack(spacing: DesignTokens.detailSpacing) {
                            TextField("输入答复", text: input)
                                .textFieldStyle(.roundedBorder)
                                .onSubmit { state.answerQuestion(message, body: input.wrappedValue.trimmingCharacters(in: .whitespacesAndNewlines)) }
                            Button("发送") { state.answerQuestion(message, body: input.wrappedValue.trimmingCharacters(in: .whitespacesAndNewlines)) }
                                .buttonStyle(.borderedProminent).tint(DesignTokens.blue)
                                .disabled(state.questionSending.contains(message.id) || input.wrappedValue.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                        }
                    }
                } else {
                    Text("等待群主或调度员答复").font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
                }
            } else {
                Text("提问选项无法读取，请稍后刷新。").font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.danger)
            }
            if let error = state.questionErrors[message.id] { Text(error).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.danger) }
        }.padding(DesignTokens.detailPadding)
            .background(DesignTokens.approvalBackground, in: RoundedRectangle(cornerRadius: DesignTokens.radiusCard))
            .overlay(RoundedRectangle(cornerRadius: DesignTokens.radiusCard).stroke(DesignTokens.approvalBorder, lineWidth: DesignTokens.boardLine))
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct ApprovalCard: View {
    @ObservedObject var state: AppState
    let message: RoomMessage
    let task: PackTask
    var pending: Bool { state.pendingApprovals.contains { $0.id == message.id } }
    var reason: Binding<String> { Binding(get: { state.rejectReasons[task.id] ?? "" }, set: { state.rejectReasons[task.id] = $0 }) }
    private var decision: RoomMessage? { state.approvalDecision(for: message) }
    private var canCreateScopedRule: Bool { state.canCreateScopedApprovalRule(for: task) }
    var body: some View {
        VStack(alignment: .leading, spacing: DesignTokens.detailSpacing) {
            if pending {
                Text("待批准 · 任务 #\(task.id)").font(DesignTokens.fontTitle).foregroundStyle(DesignTokens.amber)
                Text(task.title).font(DesignTokens.fontTitle)
                Text(message.body).font(DesignTokens.fontSmall).textSelection(.enabled)
                TextField("拒绝原因（可选）", text: reason).textFieldStyle(.roundedBorder).font(DesignTokens.fontSmall)
                HStack(spacing: DesignTokens.detailSpacing) {
                    Button("仅这一次") { state.approveTask(task.id) }.buttonStyle(.borderedProminent).tint(DesignTokens.blue)
                    Button("总是允许") { state.approveAlways(task, requestID: message.id) }
                        .buttonStyle(.bordered)
                        .disabled(!canCreateScopedRule)
                        .help("需要任务包含动作标记（如 [ENG]）并指派成员")
                    Button("拒绝") { state.rejectTask(task.id) }.buttonStyle(.bordered).foregroundStyle(DesignTokens.danger)
                }.font(DesignTokens.fontSmall).disabled(state.decisionsPending.contains(task.id))
                if !canCreateScopedRule {
                    Text("总是允许需要明确的动作标记（如 [ENG]）和已指派成员；当前可选择仅批准这一次。")
                        .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
                }
            } else if let decision {
                Text(decision.body.hasPrefix("批准") ? (decision.body.contains("规则库") ? "✓ 已批准 · 规则自动通过" : (state.alwaysRuleSucceeded.contains(message.id) ? "✓ 已批准 · 总是允许" : "✓ 已批准 · 仅这一次")) : "已拒绝").font(DesignTokens.fontSmall)
            } else {
                Text("已结束 · 未找到决议记录").font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
            }
            if state.alwaysRuleInFlight.contains(message.id) { Text("正在保存总是允许规则…").font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText) }
            if let note = state.alwaysRuleErrors[message.id] { Text(note).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.danger) }
            if let note = state.decisionErrors[task.id] { Text(note).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.danger) }
        }.animation(DesignTokens.animStd, value: pending)
            .padding(DesignTokens.detailPadding)
            .background(DesignTokens.approvalBackground, in: RoundedRectangle(cornerRadius: DesignTokens.radiusCard))
            .overlay(RoundedRectangle(cornerRadius: DesignTokens.radiusCard).stroke(DesignTokens.approvalBorder, lineWidth: DesignTokens.boardLine))
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct DeliveryCard: View {
    @ObservedObject var state: AppState
    let task: PackTask
    @FocusState private var reasonFocused: Bool
    var body: some View {
        VStack(alignment: .leading, spacing: DesignTokens.detailSpacing) {
            HStack { Text("交付成果 · #\(task.id)").font(DesignTokens.fontTitle); Spacer(); Text(task.statusLabel).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText) }
            Text(task.result ?? task.envelope?.summary ?? "").font(DesignTokens.fontUI).textSelection(.enabled)
            if let artifacts = task.envelope?.artifacts, !artifacts.isEmpty {
                ArtifactGroup(state: state, taskId: task.id, artifacts: artifacts)
            }
            if let reason = state.reviewNotes[task.id], task.status != "review" { Text("已打回：\(reason)").font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.danger) }
            if task.status == "review" {
                if state.reviewInputTaskId == task.id {
                    TextField("填写打回理由，回车提交", text: $state.reviewInput)
                        .focused($reasonFocused).onSubmit { state.reviewTask(task.id, approve: false, note: state.reviewInput) }
                        .onExitCommand { state.reviewInputTaskId = nil; state.reviewInput = "" }
                } else {
                    HStack {
                        Button("通过") { state.reviewTask(task.id, approve: true) }.buttonStyle(.borderedProminent).tint(DesignTokens.ink)
                        Button("打回") { state.reviewInput = ""; state.reviewInputTaskId = task.id; reasonFocused = true }.buttonStyle(.plain)
                    }.font(DesignTokens.fontSmall)
                }
            }
            if state.lastActionNote.hasPrefix("操作未完成") || state.lastActionNote == "请填写打回理由" { Text(state.lastActionNote).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.danger) }
        }.padding(DesignTokens.detailPadding).frame(maxWidth: .infinity, alignment: .leading)
            .background(DesignTokens.shellSurface, in: RoundedRectangle(cornerRadius: DesignTokens.radiusCard))
            .overlay(RoundedRectangle(cornerRadius: DesignTokens.radiusCard).stroke(DesignTokens.hairline, lineWidth: DesignTokens.boardLine))
    }
}

struct ArtifactGroup: View {
    @ObservedObject var state: AppState
    let taskId: Int
    let artifacts: [[String: Any]]
    private func url(_ item: [String: Any]) -> URL? {
        if let path = item["path"] as? String { return URL(fileURLWithPath: path) }
        if let text = item["url"] as? String, let u = URL(string: text), ["https", "http"].contains(u.scheme?.lowercased() ?? "") { return u }
        return nil
    }
    var body: some View {
        LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: DesignTokens.detailSpacing) {
            ForEach(Array(artifacts.enumerated()), id: \.offset) { index, item in
                if state.selectedArtifacts[taskId] == nil || state.selectedArtifacts[taskId] == index {
                    if let target = url(item) {
                        VStack(alignment: .leading, spacing: DesignTokens.detailSpacing) {
                            if (item["kind"] as? String)?.lowercased().contains("image") == true {
                                Button { state.artifactPreviewURL = target; state.showArtifactPreview = true } label: {
                                    ArtifactImage(url: target).frame(height: DesignTokens.artifactPreviewSize).clipped()
                                        .overlay(alignment: .bottomTrailing) {
                                            Image(systemName: "arrow.down").foregroundStyle(DesignTokens.ink).padding(DesignTokens.detailPadding)
                                        }
                                }.buttonStyle(.plain)
                            } else {
                                Button { NSWorkspace.shared.open(target) } label: {
                                    Label(target.lastPathComponent, systemImage: "doc").font(DesignTokens.fontSmall)
                                }.buttonStyle(.plain)
                            }
                            if artifacts.count > 1 {
                                Button(state.selectedArtifacts[taskId] == index ? "已选定" : "选这张") { state.selectedArtifacts[taskId] = index }
                                    .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.blue)
                            }
                        }
                    } else { Text("产物引用不可用").font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText) }
                }
            }
        }
    }
}

struct ArtifactImage: View {
    let url: URL
    var body: some View {
        Group {
            if url.isFileURL {
                if let image = NSImage(contentsOf: url) { Image(nsImage: image).resizable().scaledToFit() }
                else { Text("图片不可用").font(DesignTokens.fontSmall) }
            } else {
                AsyncImage(url: url) { image in image.resizable().scaledToFit() } placeholder: { Text("图片加载中").font(DesignTokens.fontSmall) }
            }
        }.frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

struct ArtifactPreview: View {
    @ObservedObject var state: AppState
    var body: some View {
        Group { if let url = state.artifactPreviewURL { ArtifactImage(url: url) } }
            .frame(width: DesignTokens.settingsWidth, height: DesignTokens.memberLoginHeight)
    }
}

struct TaskBoard: View {
    @ObservedObject var state: AppState
    @Namespace private var boardMotion
    private let groups: [(String, [String])] = [
        ("待认领", ["open", "released"]), ("进行中", ["claimed", "working", "blocked"]),
        ("待验收", ["review"]), ("已完成", ["done"])
    ]
    private var filtered: [PackTask] {
        if state.boardFilter == "全部" { return state.tasks }
        if state.boardFilter == "我派的" { return state.tasks.filter { ["owner", "dispatcher"].contains($0.createdBy ?? "") } }
        return state.tasks.filter { $0.assignee == state.boardFilter }
    }
    var body: some View {
        VStack(alignment: .leading, spacing: DesignTokens.boardGap) {
            HStack(spacing: DesignTokens.boardGap) {
                filter("全部")
                ForEach(state.members.filter { $0.role == "member" }) { member in filter(member.name) }
                filter("我派的")
                Spacer(minLength: 0)
            }.font(DesignTokens.fontUI).frame(height: DesignTokens.boardTabHeight)
            GeometryReader { geometry in
                let width = max(0, (geometry.size.width - DesignTokens.boardGap * 3) / 4)
                HStack(alignment: .top, spacing: DesignTokens.boardGap) {
                    ForEach(groups, id: \.0) { title, statuses in
                        let items = filtered.filter { statuses.contains($0.status) }
                        VStack(alignment: .leading, spacing: DesignTokens.boardGap) {
                            HStack { Text(title).font(DesignTokens.fontTitle); Text("\(items.count)").font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText); Spacer(minLength: 0) }
                            ScrollView(.vertical) {
                                LazyVStack(spacing: DesignTokens.boardGap) {
                                    ForEach(items) { task in
                                        BoardTaskCard(state: state, task: task)
                                            .matchedGeometryEffect(id: task.id, in: boardMotion)
                                            .transition(.opacity.combined(with: .offset(y: -DesignTokens.motionOffset)))
                                    }
                                }.frame(maxWidth: .infinity)
                            }
                        }.frame(width: width, height: geometry.size.height, alignment: .top)
                    }
                }
            }
        }.padding(DesignTokens.boardInset).frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
    private func filter(_ value: String) -> some View {
        Button { state.boardFilter = value } label: {
            VStack(spacing: 0) {
                Text(value == "全部" || value == "我派的" ? value : "\(value) 的").font(DesignTokens.fontUI)
                    .foregroundStyle(state.boardFilter == value ? DesignTokens.ink : DesignTokens.subText)
                    .frame(height: DesignTokens.boardTabHeight)
                Rectangle().fill(state.boardFilter == value ? DesignTokens.ink : Color.clear).frame(height: DesignTokens.boardLine)
            }
        }.buttonStyle(.plain).fixedSize(horizontal: true, vertical: false)
    }
}

struct BoardTaskCard: View {
    @ObservedObject var state: AppState
    let task: PackTask
    private var member: MemberInfo? { state.members.first { $0.name == task.assignee } }
    private var tag: String? {
        guard task.title.hasPrefix("["), let end = task.title.firstIndex(of: "]") else { return nil }
        return String(task.title[...end])
    }
    var body: some View {
        VStack(alignment: .leading, spacing: DesignTokens.boardCardGap) {
            HStack(alignment: .top, spacing: DesignTokens.boardCardGap) {
                Text("#\(task.id) \(task.title)").font(DesignTokens.fontTitle).foregroundStyle(task.status == "done" ? DesignTokens.subText : DesignTokens.ink)
                    .fixedSize(horizontal: false, vertical: true)
                if task.urgent { UrgentBadge() }
                Spacer(minLength: 0)
                if let tag { Text(tag).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.faintText).lineLimit(1) }
            }
            Text((task.status == "open" ? (task.reviewNote ?? state.reviewNotes[task.id]) : nil).map { "打回：\($0)" } ?? (task.status == "review" ? (task.envelope?.summary ?? task.result ?? task.body) : task.body))
                .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText).lineLimit(1)
            if let ahead = task.queueAhead {
                Text(ahead == 0 ? "队首" : "前面还有 \(ahead) 个")
                    .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
                    .padding(.horizontal, DesignTokens.urgentHorizontal).padding(.vertical, DesignTokens.urgentVertical)
                    .background(DesignTokens.bubbleAI, in: Capsule())
            }
            Rectangle().fill(DesignTokens.hairline).frame(height: DesignTokens.boardLine)
            HStack(spacing: DesignTokens.boardCardGap) {
                if let name = task.assignee {
                    MemberAvatarView(name: name, member: member, size: DesignTokens.boardAvatar, showStatus: false).id(state.avatarRevision)
                    Text(name).lineLimit(1)
                } else { Text("未指派") }
                Spacer(minLength: 0)
                Text(task.status == "done" ? "已关单" : task.statusLabel)
                    .foregroundStyle(task.status == "blocked" ? DesignTokens.danger : DesignTokens.subText).lineLimit(1)
            }.font(DesignTokens.fontSmall)
            if ["claimed", "working"].contains(task.status) {
                Rectangle().fill(DesignTokens.hairline).frame(height: DesignTokens.boardProgressHeight)
            }

        }.padding(DesignTokens.boardCardInset)
            .background(DesignTokens.shellSurface, in: RoundedRectangle(cornerRadius: DesignTokens.boardCardRadius))
            .overlay(RoundedRectangle(cornerRadius: DesignTokens.boardCardRadius).stroke(DesignTokens.hairline, lineWidth: DesignTokens.boardLine))
            .contentShape(Rectangle())
            .onTapGesture { state.openTaskDetail(task.id) }.help("查看任务详情")
    }
}

struct ApprovalsView: View {
    @ObservedObject var state: AppState
    private var reviews: [PackTask] { state.tasks.filter { $0.status == "review" } }
    private var offline: [MemberInfo] {
        state.members.filter { member in
            member.role == "member" && member.name != "owner" && member.lastHeartbeat.map { Date().timeIntervalSince1970 - $0 >= DesignTokens.offlineAlertSeconds } == true
        }
    }
    private func jump(_ message: RoomMessage) {
        state.highlightedMessageId = message.id; state.selection = "room"
    }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: DesignTokens.settingsGap) {
                Text("收件箱 · 当前房间").font(DesignTokens.fontTitle)
                Text("待批准 · \(state.pendingApprovals.count)").font(DesignTokens.fontTitle)
                if state.pendingApprovals.isEmpty { Text("暂无待批准").font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText) }
                ForEach(state.pendingApprovals) { message in
                    Button { jump(message) } label: { Label(message.body, systemImage: "checkmark.shield").frame(maxWidth: .infinity, alignment: .leading) }
                        .buttonStyle(.plain).padding(DesignTokens.memberInset).overlay(RoundedRectangle(cornerRadius: DesignTokens.radiusCard).stroke(DesignTokens.hairline))
                }
                Text("交活待验收 · \(reviews.count)").font(DesignTokens.fontTitle)
                if reviews.isEmpty { Text("暂无待验收").font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText) }
                ForEach(reviews) { task in
                    Button { state.openTaskDetail(task.id) } label: { Label("#\(task.id) \(task.title)", systemImage: "doc.text").frame(maxWidth: .infinity, alignment: .leading) }
                        .buttonStyle(.plain)
                        .padding(DesignTokens.memberInset).overlay(RoundedRectangle(cornerRadius: DesignTokens.radiusCard).stroke(DesignTokens.hairline))
                }
                Text("成员掉线超过 10 分钟 · \(offline.count)").font(DesignTokens.fontTitle)
                if offline.isEmpty { Text("暂无掉线成员").font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText) }
                ForEach(offline) { member in
                    Button { state.toggleMember(member.name) } label: { Label("\(member.name) · 已离线", systemImage: "person.crop.circle.badge.exclamationmark").frame(maxWidth: .infinity, alignment: .leading) }
                        .buttonStyle(.plain).padding(DesignTokens.memberInset).overlay(RoundedRectangle(cornerRadius: DesignTokens.radiusCard).stroke(DesignTokens.hairline))
                }
            }.padding(DesignTokens.settingsInset).frame(maxWidth: DesignTokens.settingsWidth, alignment: .leading).frame(maxWidth: .infinity, alignment: .center)
        }
    }
}

