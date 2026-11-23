import Foundation
import SwiftUI
import PackCore

@MainActor
extension AppState {
    // MARK: - Approval decisions and review

    /// approval_request messages whose task is still waiting and has not
    /// been approved or rejected by the dispatcher afterwards.
    func approvalDecision(for request: RoomMessage) -> RoomMessage? {
        guard let tid = request.taskId else { return nil }
        for item in messages where item.id > request.id {
            if item.kind == "approval_request" && item.taskId == tid { return nil }
            guard item.author == "dispatcher" else { continue }
            for prefix in ["批准 任务#\(tid)", "拒绝 任务#\(tid)"] where item.body.hasPrefix(prefix) {
                let tail = item.body.dropFirst(prefix.count)
                if tail.first?.isNumber != true { return item }
            }
        }
        return nil
    }

    var pendingApprovals: [RoomMessage] {
        messages.filter { m in
            guard m.kind == "approval_request", let tid = m.taskId,
                  let task = tasks.first(where: { $0.id == tid }),
                  task.status == "claimed" || task.status == "working" else { return false }
            let newerRequest = messages.contains { $0.id > m.id && $0.kind == "approval_request" && $0.taskId == tid }
            return !newerRequest && approvalDecision(for: m) == nil
        }
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

}
