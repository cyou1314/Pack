import SwiftUI
import PackCore

@MainActor
extension AppState {
    // MARK: - Task lifecycle and actions

    func createTaskFromDraft() {
        guard !inlineTaskSending else { return }
        guard attachments.isEmpty else { inlineTaskError = "派活暂不支持图片，请先移除所选图片或关闭派活。"; return }
        let original = draftMessage
        guard !original.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        guard let client else { inlineTaskError = "中枢未连接，草稿已保留"; return }
        let parts = original.split(separator: "\n", maxSplits: 1, omittingEmptySubsequences: false)
        var title = String(parts[0]).trimmingCharacters(in: .whitespaces)
        guard !title.isEmpty else { inlineTaskError = "请在首行填写任务标题"; return }
        let body = parts.count > 1 ? String(parts[1]) : ""
        let tag = "【需批准】"
        if newNeedsApproval && !title.contains(tag) { title = tag + title }
        let assignee = newAssignee
        let room = currentRoom
        let urgent = newUrgent
        inlineTaskError = ""
        inlineTaskSending = true
        Task {
            defer { inlineTaskSending = false }
            do {
                _ = try await client.call(name: "task_create", args: ["room": room, "title": title, "body": body, "assignee_name": assignee, "urgent": urgent])
                newUrgent = false
                if currentRoom == room && draftMessage == original {
                    draftMessage = ""
                    showInlineTask = false
                    newNeedsApproval = approvalDefault
                } else if currentRoom != room && roomDrafts[room] == original {
                    roomDrafts[room] = ""
                    UserDefaults.standard.set(roomDrafts, forKey: "pack.roomDrafts")
                }
                lastActionNote = "已派活"
                await refresh()
            } catch {
                inlineTaskError = "派活结果未确认，请先查看任务板，草稿已保留"
            }
        }
    }

    func createTask() {
        let tag = "\u{3010}\u{9700}\u{6279}\u{51C6}\u{3011}"
        var title = newTitle.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !title.isEmpty else { return }
        if newNeedsApproval && !title.contains(tag) { title = tag + title }
        let body = newBody
        let assignee = newAssignee
        let room = currentRoom
        showCreateTask = false
        newTitle = ""; newBody = ""; newNeedsApproval = approvalDefault
        act("已派活") { try await $0.call(name: "task_create",
                                          args: ["room": room, "title": title, "body": body, "assignee_name": assignee]) }
    }

    func openTaskDetail(_ tid: Int) {
        selectedTaskId = tid
        selection = "tasks"
        taskEvents = []; taskRuns = []; taskTransfers = []; taskEventsTruncated = false
        detailLoadNote = "正在读取任务记录…"
        guard let client else { detailLoadNote = "记录暂时无法读取"; return }
        Task {
            do {
                let audit = try await client.call(name: "task_audit", args: ["task_id": tid])
                guard selectedTaskId == tid else { return }
                taskEvents = (audit["events"] as? [[String: Any]]) ?? []
                taskEventsTruncated = boolVal(audit["events_truncated"])
                taskRuns = (audit["runs"] as? [[String: Any]]) ?? []
                taskTransfers = (audit["transfers"] as? [[String: Any]]) ?? []
                detailLoadNote = taskEventsTruncated ? "审计记录仅显示前 200 条" : ""
            } catch { if selectedTaskId == tid { detailLoadNote = "记录暂时无法读取，请重新打开任务重试" } }
        }
    }

    func toggleChatTask(_ tid: Int) { openTaskDetail(tid) }

    func setTaskUrgent(_ task: PackTask) {
        guard !urgentUpdating.contains(task.id) else { return }
        guard let client else { urgentErrors[task.id] = "中枢未连接"; return }
        let tid = task.id
        urgentUpdating.insert(tid)
        urgentErrors[tid] = nil
        Task {
            defer { urgentUpdating.remove(tid) }
            do {
                _ = try await client.call(name: "task_set_urgent", args: ["task_id": tid, "urgent": !task.urgent])
                await refresh()
            } catch let error as HubError {
                switch error {
                case .toolError(let message), .badResponse(let message): urgentErrors[tid] = message
                }
            } catch { urgentErrors[tid] = "加急状态未能更新，请检查连接后重试" }
        }
    }

    func cancelTask(_ tid: Int) {
        act("已取消任务 #\(tid)") { try await $0.call(name: "task_cancel", args: ["task_id": tid]) }
    }
}
