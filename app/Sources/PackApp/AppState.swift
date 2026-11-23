import SwiftUI
import Security
import PackCore

@MainActor
final class AppState: ObservableObject {
    @Published var messages: [RoomMessage] = []
    @Published var tasks: [PackTask] = []
    @Published var members: [MemberInfo] = []
    @Published var connectionNote: String = "未连接"
    @Published var connected: Bool = false
    @Published var connectionFailed: Bool = false
    @Published var roomSummary: RoomSummary?
    @Published var roomLoading = true
    @Published var unreadCounts: [String: Int] = [:]
    @Published var summaryExpanded = true
    @Published var showAddMenu = false
    @Published var showSearch = false
    @Published var notificationStatus = "正在检查系统通知状态"
    @Published var selection: String? = "room"
    @Published var selectedTaskId: Int?
    @Published var boardFilter: String = "全部"
    @Published var highlightedMessageId: Int?
    @Published var expandedChatTaskId: Int?
    @Published var reviewInputTaskId: Int?
    @Published var reviewInput: String = ""
    @Published var reviewNotes: [Int: String] = [:]
    @Published var taskEvents: [[String: Any]] = []
    @Published var taskEventsTruncated = false
    @Published var taskRuns: [[String: Any]] = []
    @Published var taskTransfers: [[String: Any]] = []
    @Published var detailLoadNote: String = ""
    @Published var hoveredMessageId: Int?
    @Published var editingMessageId: Int?
    @Published var editingMessageText = ""
    @Published var currentRoom = "pack-main"
    @Published var rooms: [RoomItem] = []
    @Published var roomDrafts: [String: String] = UserDefaults.standard.dictionary(forKey: "pack.roomDrafts") as? [String: String] ?? [:]
    @Published var showRoomDialog = false
    @Published var roomDialogName = ""
    @Published var roomRenameFrom: String?
    @Published var roomDialogError = ""
    @Published var memberRunDurations: [Int: TimeInterval] = [:]
    @Published var memberRunsLoading = false
    @Published var memberSkills: [AssignedSkill] = []
    @Published var memberSkillsStatus = ""
    @Published var showAvatarPicker = false
    @Published var avatarRevision = 0
    @Published var claimingMembers: Set<String> = []
    @Published var hoveredMenuItem: String?
    @Published var avatarTargetName = ""
    @Published var avatarSaving = false
    @Published var avatarSaveError = ""
    @Published var editAvatarSpecies = "fox"
    @Published var editAvatarPalette = "orange"
    @Published var editAvatarAccessory = "none"
    @Published var editAvatarBackground = "cream"
    @Published var decisionsPending: Set<Int> = []
    @Published var decisionErrors: [Int: String] = [:]
    @Published var questionInputs: [Int: String] = [:]
    @Published var questionSending: Set<Int> = []
    @Published var questionErrors: [Int: String] = [:]
    @Published var questionResponderName: String?
    @Published var localQuestionAnswers: [Int: (author: String, body: String)] = [:]
    @Published var alwaysRuleInFlight: Set<Int> = []
    @Published var alwaysRuleErrors: [Int: String] = Dictionary(uniqueKeysWithValues:
        (UserDefaults.standard.dictionary(forKey: "pack.alwaysRuleErrors") ?? [:]).compactMap { key, value in
            guard let id = Int(key), let text = value as? String else { return nil }
            return (id, text)
        }) {
        didSet { UserDefaults.standard.set(Dictionary(uniqueKeysWithValues: alwaysRuleErrors.map { (String($0.key), $0.value) }), forKey: "pack.alwaysRuleErrors") }
    }
    @Published var alwaysRuleSucceeded = Set(UserDefaults.standard.array(forKey: "pack.alwaysRuleSucceeded") as? [Int] ?? []) {
        didSet { UserDefaults.standard.set(Array(alwaysRuleSucceeded), forKey: "pack.alwaysRuleSucceeded") }
    }
    @Published var memberRunsFailed: Set<Int> = []
    @Published var selectedMemberName: String?
    @Published var showAddMember = false
    @Published var memberNameInput = ""
    @Published var memberActionError = ""
    @Published var avatarSpecies = "fox"
    @Published var avatarPalette = "orange"
    @Published var avatarAccessory = "none"
    @Published var avatarBackground = "cream"
    @Published var hoveredRoom: String?
    @Published var expandedMessages: Set<Int> = []
    @Published var lastSeenByRoom: [String: Int] = UserDefaults.standard.dictionary(forKey: "pack.lastSeen") as? [String: Int] ?? [:]
    @Published var unreadBoundary: Int?
    @Published var showCatalog = false
    @Published var catalogKind = ""
    @Published var catalogItems: [[String: Any]] = []
    @Published var catalogNote = ""
    @Published var catalogArgument = ""
    @Published var attachmentsByRoom: [String: [PendingAttachment]] = [:]
    @Published var attachmentNoticeByRoom: [String: String] = [:]
    @Published var attachmentImages: [Int: NSImage] = [:]
    @Published var attachmentFailures: Set<Int> = []
    var attachmentLoading: Set<Int> = []
    var attachments: [PendingAttachment] { attachmentsByRoom[currentRoom] ?? [] }
    @Published var artifactPreviewURL: URL?
    @Published var showArtifactPreview = false
    @Published var selectedArtifacts: [Int: Int] = [:]
    @Published var showMemberLogin = false
    @Published var loginTargetName = ""
    @Published var loginTargetProvider: String?
    @Published var memberLoginSaving = false
    @Published var memberHandoffInProgress: String?
    @Published var memberHandoffResults: [String: String] = UserDefaults.standard.dictionary(forKey: "pack.memberHandoffResults") as? [String: String] ?? [:]
    @Published var memberRemovalInProgress = false
    @Published var memberRemovalReport = UserDefaults.standard.string(forKey: "pack.memberRemovalReport") ?? ""
    @Published var showRemoveMember = false
    @Published var pendingMembers: [String] = UserDefaults.standard.stringArray(forKey: "pack.pendingMembers") ?? []
    @Published var draftMessage: String = "" {
        didSet {
            roomDrafts[currentRoom] = draftMessage
            UserDefaults.standard.set(roomDrafts, forKey: "pack.roomDrafts")
        }
    }
    @Published var replyByRoom: [String: ReplySnapshot] = [:]
    @Published var sendingReply: ReplySnapshot?
    @Published var sendingMessage: String?
    @Published var sendingRoom: String?
    @Published var sendingFailedID: UUID?
    @Published var failedMessagesByRoom: [String: [FailedChatMessage]] = [:]
    @Published var failedMessageNotices: [UUID: String] = [:]
    /// Draft text at the moment the user dismissed the @-mention popup
    /// (Esc / click elsewhere); the popup stays hidden while the draft
    /// still equals this, and reappears as soon as the text changes.
    @Published var mentionDismissedText: String?
    @Published var composerFocusRequest = 0
    @Published var showCreateTask: Bool = false
    @Published var showInlineTask: Bool = false
    @Published var inlineTaskSending = false
    @Published var inlineTaskError = ""
    @Published var showSidebar: Bool = true
    @Published var sidebarPinned: Bool = false
    @Published var roomExpanded: Bool = true
    @Published var newTitle: String = ""
    @Published var newBody: String = ""
    @Published var newAssignee: String = "absent"
    @Published var newNeedsApproval: Bool = false
    @Published var newUrgent = false
    @Published var urgentUpdating: Set<Int> = []
    @Published var urgentErrors: [Int: String] = [:]
    @Published var rejectReasons: [Int: String] = [:]
    @Published var lastActionNote: String = ""
    @Published var settingsHubURL = ""
    @Published var settingsMessage = ""
    @Published var settingsAddrToken = ""
    @Published var addrTokenStatus = "地址源令牌未配置"
    @Published var credentialStatus = "正在检查凭证…"
    @Published var showOnboarding = false
    @Published var approvalDefault: Bool = UserDefaults.standard.bool(forKey: "pack.approvalDefault") {
        didSet { UserDefaults.standard.set(approvalDefault, forKey: "pack.approvalDefault") }
    }
    @Published var appRefreshSeconds: Int = (UserDefaults.standard.integer(forKey: "pack.appRefreshSeconds") > 0 ? UserDefaults.standard.integer(forKey: "pack.appRefreshSeconds") : 15) {
        didSet { UserDefaults.standard.set(appRefreshSeconds, forKey: "pack.appRefreshSeconds") }
    }

    var client: HubClient?
    var ownerClient: HubClient?
    private var pollTask: Task<Void, Never>?
    var storedHubURL = ""
    var manualHubURLSaved = false
    var lastHubDiscoveryUptime: TimeInterval?
    var discoveryInProgress = false
    var clientGeneration = 0
    private var consecutiveRefreshFailures = 0
    let hubDiscoveryInterval: TimeInterval = 60
    let hubDiscoveryTimeout: TimeInterval = 5
    private let taskListPageSize = 100
    private let taskListMaxPages = 10
    var roomGeneration = 0
    private var unreadTask: Task<Void, Never>?
    var notificationDiff = NotificationDiff()
    var decisionRooms: [Int: String] = [:]
    var pendingNotification: (kind: String, room: String, id: Int?, member: String?)?

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

    func refresh() async {
        guard let client else { return }
        let room = currentRoom
        let generation = roomGeneration
        let currentClientGeneration = clientGeneration
        do {
            let roomRes = try await client.call(name: "room_list")
            let msgRes = try await client.call(name: "room_read_new",
                                               args: ["room": room, "since_id": 0, "limit": 200, "tail": true])
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
            guard let arr = msgRes["messages"] as? [[String: Any]] else { throw HubError.badResponse("missing messages") }
            do {
                let parsedMessages = arr.map {
                    RoomMessage(id: intVal($0["id"]), author: ($0["author"] as? String) ?? "?",
                                kind: ($0["kind"] as? String) ?? "chat", body: ($0["body"] as? String) ?? "",
                                taskId: optIntVal($0["task_id"]), createdAt: doubleVal($0["created_at"]),
                                deleted: boolVal($0["deleted"]), edited: boolVal($0["edited"]),
                                attachments: (($0["attachments"] as? [[String: Any]]) ?? []).compactMap { item in
                                    guard let id = optIntVal(item["id"]), id > 0, let mime = item["mime"] as? String else { return nil }
                                    return MessageAttachment(id: id, mime: mime, size: intVal(item["size"]), filename: (item["filename"] as? String) ?? "图片")
                                }, replyToId: optIntVal($0["reply_to_id"]), questionPayload: QuestionPayload.parse($0["payload"]))
                }
                if roomLoading { messages = parsedMessages }
                else { withAnimation(DesignTokens.animStd) { messages = parsedMessages } }
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

    // MARK: - derived

    var selectedTask: PackTask? { tasks.first { $0.id == selectedTaskId } }

    func loadCatalog(_ kind: String) {
        guard let client else { return }
        catalogKind = kind; catalogItems = []; catalogNote = "加载中…"; showCatalog = true
        Task {
            do {
                let result = try await client.call(name: kind == "技能" ? "skill_list" : "template_list")
                catalogItems = (result[kind == "技能" ? "skills" : "templates"] as? [[String: Any]]) ?? []
                catalogNote = catalogItems.isEmpty ? "暂无\(kind)" : ""
            } catch { catalogNote = "读取未完成，请检查连接后重试" }
        }
    }

    func fireTemplate(_ name: String) {
        let room = currentRoom; let argument = catalogArgument
        act("已从模板创建任务") { try await $0.call(name: "template_fire", args: ["name": name, "arg": argument, "room": room]) }
        showCatalog = false; catalogArgument = ""
    }

    // MARK: - actions

    func act(_ note: String, _ op: @escaping (HubClient) async throws -> [String: Any]) {
        guard let client else { return }
        Task {
            do {
                _ = try await op(client)
                lastActionNote = note
                await refresh()
            } catch {
                lastActionNote = "操作未完成，请检查连接后重试"
            }
        }
    }


}
