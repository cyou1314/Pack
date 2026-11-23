import SwiftUI
import AppKit
import UniformTypeIdentifiers
import WebKit

struct TaskDetail: View {
    @ObservedObject var state: AppState
    let task: PackTask
    @FocusState private var reasonFocused: Bool
    var body: some View {
        VStack(alignment: .leading, spacing: DesignTokens.detailSpacing) {
            HStack {
                Text("任务 #\(task.id) · \(task.title)").font(DesignTokens.fontTitle)
                if task.urgent { UrgentBadge() }
                Spacer()
                Text(task.statusLabel).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
            }
            if !["done", "cancelled"].contains(task.status) {
                Button(task.urgent ? "取消加急" : "标为加急") { state.setTaskUrgent(task) }
                    .buttonStyle(.bordered).disabled(state.urgentUpdating.contains(task.id))
            }
            if let error = state.urgentErrors[task.id] {
                Text("加急状态更新失败：\(error)").font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.danger).textSelection(.enabled)
            }
            if !task.body.isEmpty { Text(task.body).font(DesignTokens.fontUI).textSelection(.enabled) }
            ChatTaskDetail(state: state, task: task)
            ForEach(state.messages.filter { $0.kind == "report" && $0.taskId == task.id && !$0.deleted && !$0.attachments.isEmpty }) { report in
                MessageAttachmentGroup(state: state, attachments: report.attachments)
            }
            if let artifacts = task.envelope?.artifacts, !artifacts.isEmpty {
                ArtifactGroup(state: state, taskId: task.id, artifacts: artifacts)
            }
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
            if ["open", "claimed", "working"].contains(task.status) { Button("取消任务", role: .destructive) { state.cancelTask(task.id) }.buttonStyle(.bordered) }
        }.frame(maxWidth: .infinity, alignment: .leading).padding(DesignTokens.detailPadding).card()
    }
}

struct CreateTaskView: View {
    @ObservedObject var state: AppState
    var body: some View {
        VStack(alignment: .leading, spacing: 15) {
            Text("派发新任务").font(.system(size: 19, weight: .semibold))
            Text("写清目标和要求，成员会在群里回报进展。").font(.system(size: 12)).foregroundStyle(.secondary)
            TextField("任务标题", text: $state.newTitle).textFieldStyle(.roundedBorder)
            TextField("任务要求和交付内容", text: $state.newBody, axis: .vertical).textFieldStyle(.roundedBorder).lineLimit(3...6)
            Picker("指派给", selection: $state.newAssignee) { ForEach(state.members.filter { $0.role == "member" }) { Text($0.name).tag($0.name) } }
            Toggle("需要我批准才能执行", isOn: $state.newNeedsApproval)
            HStack { Spacer(); Button("取消") { state.showCreateTask = false }; Button("建卡派活") { state.createTask() }.buttonStyle(.borderedProminent).disabled(state.newTitle.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty) }
        }.padding(24).frame(width: 470)
    }
}
