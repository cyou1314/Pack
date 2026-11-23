import Foundation
import SwiftUI
import Security
import PackCore

@MainActor
extension AppState {
    // MARK: - App startup, polling and refresh lifecycle

    func start() {
        guard pollTask == nil else { return }
        PackNotifications.shared.start(state: self)
        draftMessage = roomDrafts[currentRoom] ?? ""
        newNeedsApproval = approvalDefault
        refreshAddrTokenStatus()
        let loaded = PackConfig.load()
        credentialStatus = loaded.credentialStatus
        guard let cfg = loaded.config else {
            connectionNote = "缺少配置 ~/.pack/config.json"
            roomLoading = false
            showOnboarding = true
            return
        }
        settingsHubURL = cfg.hubUrl
        storedHubURL = cfg.hubUrl
        client = HubClient(cfg: cfg)
        if let ownerToken = cfg.ownerToken, !ownerToken.isEmpty {
            var ownerCfg = cfg
            ownerCfg.token = ownerToken
            ownerClient = HubClient(cfg: ownerCfg)
        }
        pollTask = Task { [weak self] in
            _ = await self?.followPublishedHubURL()
            while !Task.isCancelled {
                await self?.refresh()
                let seconds = max(5, self?.appRefreshSeconds ?? 15)
                try? await Task.sleep(nanoseconds: UInt64(seconds) * 1_000_000_000)
            }
        }
    }

    func refresh(forceFullMessageSnapshot: Bool = false) async {
        guard let client else { return }
        let room = currentRoom
        let generation = roomGeneration
        let currentClientGeneration = clientGeneration
        do {
            let roomRes = try await client.call(name: "room_list")
            let fullMessageRefresh = messageRefreshState.requiresFullSnapshot(
                roomLoading: roomLoading,
                force: forceFullMessageSnapshot,
                now: ProcessInfo.processInfo.systemUptime,
                clientGeneration: currentClientGeneration,
                roomGeneration: generation
            )
            let messageRows: [[String: Any]]
            var didFullMessageRefresh = fullMessageRefresh
            if fullMessageRefresh {
                let result = try await client.call(name: "room_read_new",
                                                    args: ["room": room, "since_id": 0,
                                                           "limit": 200, "tail": true])
                guard let rows = result["messages"] as? [[String: Any]] else {
                    throw HubError.badResponse("missing messages")
                }
                messageRows = rows
            } else {
                var rows: [[String: Any]] = []
                var cursor = messages.last?.id ?? 0
                var exhaustedPageBudget = false
                for page in 0..<MessageRefreshState.maximumIncrementalPages {
                    let result = try await client.call(
                        name: "room_read_new",
                        args: ["room": room, "since_id": cursor,
                               "limit": MessageRefreshState.incrementalPageLimit, "tail": false]
                    )
                    guard let batch = result["messages"] as? [[String: Any]] else {
                        throw HubError.badResponse("missing incremental messages")
                    }
                    rows.append(contentsOf: batch)
                    guard MessageRefreshState.needsAnotherPage(receivedCount: batch.count) else {
                        break
                    }
                    guard let last = batch.last else { break }
                    let nextCursor = intVal(last["id"])
                    guard nextCursor > cursor else {
                        throw HubError.badResponse("message cursor did not advance")
                    }
                    cursor = nextCursor
                    if page == MessageRefreshState.maximumIncrementalPages - 1 {
                        exhaustedPageBudget = true
                    }
                }

                if exhaustedPageBudget {
                    // Sustained traffic can outpace incremental paging. Fall
                    // back to the newest tail so current questions/approvals
                    // cannot remain hidden behind an old cursor.
                    let result = try await client.call(
                        name: "room_read_new",
                        args: ["room": room, "since_id": 0, "limit": 200, "tail": true]
                    )
                    guard let tail = result["messages"] as? [[String: Any]] else {
                        throw HubError.badResponse("missing message tail")
                    }
                    messageRows = tail
                    didFullMessageRefresh = true
                } else {
                    messageRows = rows
                }
            }
            let taskRows = try await loadTaskPages(client, room: room)
            let memRes = try await client.call(name: "member_list")
            let summaryRes = try? await client.call(name: "room_summary_get", args: ["room": room])
            guard room == currentRoom, generation == roomGeneration, currentClientGeneration == clientGeneration else { return }
            if let summaryRes { roomSummary = summaryVal(summaryRes) }
            if let arr = roomRes["rooms"] as? [[String: Any]] {
                let updated = arr.map { RoomItem(id: intVal($0["id"]), name: ($0["name"] as? String) ?? "?", lastMessageId: intVal($0["last_message_id"])) }.reversed()
                for item in updated where rooms.first(where: { $0.id == item.id })?.lastMessageId != item.lastMessageId {
                    unreadCounts.removeValue(forKey: item.name)
                }
                rooms = Array(updated)
            }
            do {
                let parsedMessages = messageRows.map {
                    RoomMessage(id: intVal($0["id"]), author: ($0["author"] as? String) ?? "?",
                                kind: ($0["kind"] as? String) ?? "chat", body: ($0["body"] as? String) ?? "",
                                taskId: optIntVal($0["task_id"]), createdAt: doubleVal($0["created_at"]),
                                deleted: boolVal($0["deleted"]), edited: boolVal($0["edited"]),
                                attachments: (($0["attachments"] as? [[String: Any]]) ?? []).compactMap { item in
                                    guard let id = optIntVal(item["id"]), id > 0, let mime = item["mime"] as? String else { return nil }
                                    return MessageAttachment(id: id, mime: mime, size: intVal(item["size"]), filename: (item["filename"] as? String) ?? "图片")
                                }, replyToId: optIntVal($0["reply_to_id"]), questionPayload: QuestionPayload.parse($0["payload"]))
                }
                if didFullMessageRefresh {
                    messageRefreshState.recordFullSnapshot(
                        at: ProcessInfo.processInfo.systemUptime,
                        clientGeneration: currentClientGeneration,
                        roomGeneration: generation
                    )
                    if roomLoading { messages = parsedMessages }
                    else { withAnimation(DesignTokens.animStd) { messages = parsedMessages } }
                } else if !parsedMessages.isEmpty {
                    var mergedByID: [Int: RoomMessage] = [:]
                    for message in messages { mergedByID[message.id] = message }
                    for message in parsedMessages { mergedByID[message.id] = message }
                    let merged = mergedByID.keys.sorted().suffix(200).compactMap { mergedByID[$0] }
                    withAnimation(DesignTokens.animStd) { messages = merged }
                }
            }
            if messages.contains(where: { $0.kind == "question" }) {
                let answerClient = ownerClient ?? client
                let identity = try? await answerClient.call(name: "heartbeat")
                guard room == currentRoom, generation == roomGeneration, currentClientGeneration == clientGeneration else { return }
                questionResponderName = identity?["member"] as? String
            }
            do {
                let arr = taskRows
                let previousTasks = tasks
                let updatedTasks = arr.map {
                    PackTask(id: intVal($0["id"]), title: ($0["title"] as? String) ?? "",
                             body: ($0["body"] as? String) ?? "", status: ($0["status"] as? String) ?? "",
                             assignee: optStringVal($0["assignee"]), attempts: intVal($0["attempts"]),
                             result: optStringVal($0["result"]), leaseExpires: optDoubleVal($0["lease_expires"]),
                             updatedAt: doubleVal($0["updated_at"]),
                             envelope: envelopeVal($0["envelope"]),
                             failClass: optStringVal($0["fail_class"]),
                             nextClaimAt: optDoubleVal($0["next_claim_at"]),
                             schedAt: optDoubleVal($0["sched_at"]),
                             createdBy: optStringVal($0["created_by"]), reviewNote: optStringVal($0["review_note"]),
                             urgent: boolVal($0["urgent"]), queueAhead: optIntVal($0["queue_ahead"]))
                }
                if !roomLoading {
                    let claimed = Set(updatedTasks.compactMap { task -> String? in
                        guard ["claimed", "working"].contains(task.status),
                              let previous = previousTasks.first(where: { $0.id == task.id }),
                              ["open", "released"].contains(previous.status) else { return nil }
                        return task.assignee
                    })
                    if !claimed.isEmpty {
                        withAnimation(DesignTokens.animFast) { claimingMembers.formUnion(claimed) }
                        Task {
                            try? await Task.sleep(nanoseconds: DesignTokens.memberClaimHighlightNanoseconds)
                            withAnimation(DesignTokens.animFast) { claimingMembers.subtract(claimed) }
                        }
                    }
                    withAnimation(DesignTokens.animStd) { tasks = updatedTasks }
                } else { tasks = updatedTasks }
            }
            if let arr = memRes["members"] as? [[String: Any]] {
                members = arr.map {
                    MemberInfo(name: ($0["name"] as? String) ?? "?", role: ($0["role"] as? String) ?? "",
                               profile: optStringVal($0["profile"]),
                               avatar: avatarVal($0["avatar"]),
                               lastHeartbeat: optDoubleVal($0["last_heartbeat"]),
                               provider: optStringVal($0["provider"]).flatMap { $0.isEmpty ? nil : $0 },
                               loginState: optStringVal($0["login_state"]),
                               kind: optStringVal($0["kind"]),
                               quotaWeekUsedPct: optDoubleVal($0["quota_week_used_pct"]),
                               quotaWeekResetAt: optDoubleVal($0["quota_week_reset_at"]),
                               quotaExtraLeft: optIntVal($0["quota_extra_left"]),
                               quotaUpdatedAt: optDoubleVal($0["quota_updated_at"]),
                               weekDone: ($0["week_done"] as? NSNumber)?.intValue)
                }
            }
            roomLoading = false
            connected = true
            consecutiveRefreshFailures = 0
            connectionFailed = false
            connectionNote = "已连接"
            for (taskId, decisionRoom) in decisionRooms where decisionRoom == room && tasks.contains(where: { $0.id == taskId }) && !pendingApprovals.contains(where: { $0.taskId == taskId }) {
                decisionsPending.remove(taskId)
                decisionRooms.removeValue(forKey: taskId)
            }
            processNotificationSnapshot(room: room)
            if let target = pendingNotification, target.room == room {
                pendingNotification = nil
                navigateNotification(kind: target.kind, id: target.id, member: target.member)
            }
            let snapshot = rooms
            if unreadTask == nil {
                unreadTask = Task { [weak self] in
                    await self?.refreshUnreadCounts(snapshot)
                    self?.unreadTask = nil
                }
            }
            scheduleMarkRoomRead(room, generation: generation)
        } catch {
            guard room == currentRoom, generation == roomGeneration, currentClientGeneration == clientGeneration else { return }
            roomLoading = false
            connected = false
            connectionFailed = true
            connectionNote = "连接中断，重试中"
            consecutiveRefreshFailures += 1
            if consecutiveRefreshFailures >= 2, await followPublishedHubURL(rebuildOnFailure: true) { await refresh() }
        }
    }

    private func loadTaskPages(_ client: HubClient, room: String) async throws -> [[String: Any]] {
        var rows: [[String: Any]] = []
        for page in 0..<taskListMaxPages {
            let result = try await client.call(name: "task_list", args: ["offset": page * taskListPageSize, "room": room])
            guard let batch = result["tasks"] as? [[String: Any]] else { throw HubError.badResponse("missing tasks") }
            rows.append(contentsOf: batch)
            if batch.count < taskListPageSize { break }
        }
        return rows
    }

}
