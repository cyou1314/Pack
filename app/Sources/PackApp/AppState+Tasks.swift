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

    func approveTask(_ tid: Int) { decideTask(tid, approve: true) }

    func approvalRuleScope(for task: PackTask) -> ApprovalRuleScope? {
        ApprovalRuleScope(taskTitle: task.title, assignee: task.assignee)
    }

    func canCreateScopedApprovalRule(for task: PackTask) -> Bool {
        approvalRuleScope(for: task) != nil
    }

    func approveAlways(_ task: PackTask, requestID: Int) {
        guard let scope = approvalRuleScope(for: task) else {
            alwaysRuleErrors[requestID] = "总是允许需要明确的动作标记（如 [ENG]）和已指派成员；为避免创建通配规则，当前任务未被批准。"
            return
        }
        alwaysRuleErrors[requestID] = nil
        decideTask(task.id, approve: true, always: (requestID, scope))
    }

    func rejectTask(_ tid: Int) { decideTask(tid, approve: false) }

    private func decideTask(_ tid: Int, approve: Bool, always: (requestID: Int, scope: ApprovalRuleScope)? = nil) {
        guard !decisionsPending.contains(tid), let client else { return }
        decisionsPending.insert(tid)
        decisionErrors[tid] = nil
        if let always {
            alwaysRuleInFlight.insert(always.requestID)
            alwaysRuleErrors[always.requestID] = nil
            alwaysRuleSucceeded.remove(always.requestID)
        }
        let room = currentRoom
        decisionRooms[tid] = room
        let reason = (rejectReasons[tid] ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let body = approve ? "批准 任务#\(tid)（群主已在 App 批准）" : "拒绝 任务#\(tid)" + (reason.isEmpty ? "" : "：\(reason)")
        Task {
            defer { if let always { alwaysRuleInFlight.remove(always.requestID) } }
            var posted = false
            do {
                _ = try await client.call(name: "room_post", args: ["room": room, "body": body, "kind": "decision"])
                posted = true
                if let always {
                    do {
                        _ = try await client.call(
                            name: "rule_upsert",
                            args: [
                                "action": always.scope.action,
                                "target": always.scope.target,
                                "effect": "allow",
                                "source": "app-always"
                            ]
                        )
                        alwaysRuleSucceeded.insert(always.requestID)
                    } catch {
                        alwaysRuleErrors[always.requestID] = "本次已批准，但总是允许规则保存未确认，请检查规则后重试。"
                    }
                }
                if !approve { _ = try await client.call(name: "task_cancel", args: ["task_id": tid]) }
                lastActionNote = approve ? "已批准任务 #\(tid)" : "已拒绝并取消任务 #\(tid)"
                await refresh()
                if room == currentRoom && connected && !pendingApprovals.contains(where: { $0.taskId == tid }) {
                    decisionsPending.remove(tid); decisionRooms.removeValue(forKey: tid)
                } else {
                    decisionErrors[tid] = "决议已发送，正在等待状态确认"
                }
            } catch {
                decisionErrors[tid] = posted && !approve ? "拒绝已发送，取消任务未确认，请检查任务状态" : "决议结果未确认，请检查房间记录后再操作"
                await refresh()
                if room == currentRoom && connected && !pendingApprovals.contains(where: { $0.taskId == tid }) {
                    decisionsPending.remove(tid); decisionRooms.removeValue(forKey: tid)
                }
            }
        }
    }

    func reviewTask(_ tid: Int, approve: Bool, note: String = "") {
        guard let client else { return }
        let reason = note.trimmingCharacters(in: .whitespacesAndNewlines)
        guard approve || !reason.isEmpty else { lastActionNote = "请填写打回理由"; return }
        Task {
            do {
                _ = try await client.call(name: "task_review", args: ["task_id": tid, "approve": approve, "note": reason])
                if !approve { reviewNotes[tid] = reason }
                reviewInputTaskId = nil
                reviewInput = ""
                lastActionNote = approve ? "已关单 #\(tid)" : "已打回 #\(tid)"
                await refresh()
            } catch { lastActionNote = "操作未完成，请检查连接后重试" }
        }
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
