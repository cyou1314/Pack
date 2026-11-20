import SwiftUI
import Security

struct RoomItem: Identifiable {
    let id: Int
    let name: String
    let lastMessageId: Int
}

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
    private var attachmentLoading: Set<Int> = []
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

    private var client: HubClient?
    private var ownerClient: HubClient?
    private var pollTask: Task<Void, Never>?
    private var storedHubURL = ""
    private var manualHubURLSaved = false
    private var lastHubDiscoveryUptime: TimeInterval?
    private var discoveryInProgress = false
    private var clientGeneration = 0
    private var consecutiveRefreshFailures = 0
    private let hubDiscoveryInterval: TimeInterval = 60
    private let hubDiscoveryTimeout: TimeInterval = 5
    private let taskListPageSize = 100
    private let taskListMaxPages = 10
    private var roomGeneration = 0
    private var unreadTask: Task<Void, Never>?
    private var notificationDiff = NotificationDiff()
    private var decisionRooms: [Int: String] = [:]
    private var pendingNotification: (kind: String, room: String, id: Int?, member: String?)?

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
                                })
                }
                if roomLoading { messages = parsedMessages }
                else { withAnimation(DesignTokens.animStd) { messages = parsedMessages } }
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
                             urgent: boolVal($0["urgent"]))
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

    private func processNotificationSnapshot(room: String) {
        let approvals = pendingApprovals
        let reviews = tasks.filter { $0.status == "review" }
        let heartbeats = Dictionary(uniqueKeysWithValues: members.compactMap { member -> (String, Double)? in
            guard member.role == "member", member.name != "owner", let heartbeat = member.lastHeartbeat else { return nil }
            return (member.name, heartbeat)
        })
        let changes = notificationDiff.changes(room: room, approvals: Set(approvals.map(\.id)), reviews: Set(reviews.map(\.id)), heartbeats: heartbeats,
                                               now: Date().timeIntervalSince1970, onlineSeconds: DesignTokens.memberOnlineSeconds, offlineSeconds: DesignTokens.offlineAlertSeconds)
        let badge = approvals.count + reviews.count
        NSApp.dockTile.badgeLabel = badge > 0 ? String(badge) : nil
        for message in approvals where changes.approvals.contains(message.id) {
            let task = tasks.first { $0.id == message.taskId }
            PackNotifications.shared.send(kind: "approval", room: room, id: message.id, title: "待批准", body: "\(message.author) · \(task?.title ?? message.body)")
        }
        for task in reviews where changes.reviews.contains(task.id) {
            PackNotifications.shared.send(kind: "review", room: room, id: task.id, title: "交活待验收", body: "\(task.assignee ?? "成员") · #\(task.id) \(task.title)")
        }
        for member in changes.offline.sorted() {
            PackNotifications.shared.send(kind: "offline", room: room, member: member, title: "成员已离线", body: "\(member) 已超过 10 分钟没有心跳")
        }
    }

    func openNotification(kind: String, room: String, id: Int?, member: String?) {
        if kind == "offline" { navigateNotification(kind: kind, id: id, member: member); return }
        guard !room.isEmpty else { return }
        if room != currentRoom || roomLoading {
            pendingNotification = (kind, room, id, member)
            if room != currentRoom { selectRoom(room) } else { Task { await refresh() } }
        } else { navigateNotification(kind: kind, id: id, member: member) }
    }

    private func navigateNotification(kind: String, id: Int?, member: String?) {
        switch kind {
        case "approval":
            if let id, messages.contains(where: { $0.id == id }) { selection = "room"; highlightedMessageId = id }
            else { selection = "approvals" }
        case "review":
            if let id, tasks.contains(where: { $0.id == id }) { openTaskDetail(id) }
            else { selection = "tasks"; selectedTaskId = nil }
        case "offline":
            selection = "members"
            selectedMemberName = member.flatMap { name in members.contains(where: { $0.name == name }) ? name : nil }
        default: break
        }
    }

    var hasUnreadRooms: Bool { rooms.contains { hasUnread($0) } }

    func hasUnread(_ room: RoomItem) -> Bool {
        room.lastMessageId > (lastSeenByRoom[room.name] ?? 0) && !(room.name == currentRoom && selection == "room" && !roomLoading)
    }

    private func refreshUnreadCounts(_ snapshot: [RoomItem]) async {
        guard let client else { return }
        for room in snapshot {
            let seen = lastSeenByRoom[room.name] ?? 0
            guard hasUnread(room) else { unreadCounts.removeValue(forKey: room.name); continue }
            do {
                var cursor = seen
                var count = 0
                while cursor < room.lastMessageId {
                    let result = try await client.call(name: "room_read_new", args: ["room": room.name, "since_id": cursor, "limit": 200])
                    guard let rows = result["messages"] as? [[String: Any]], !rows.isEmpty else { break }
                    let inSnapshot = rows.filter { intVal($0["id"]) <= room.lastMessageId }
                    count += inSnapshot.count
                    guard let last = rows.last.map({ intVal($0["id"]) }), last > cursor else { break }
                    cursor = last
                }
                guard (lastSeenByRoom[room.name] ?? 0) == seen,
                      rooms.first(where: { $0.id == room.id })?.lastMessageId == room.lastMessageId,
                      hasUnread(room), cursor >= room.lastMessageId else { continue }
                unreadCounts[room.name] = count
            } catch { unreadCounts.removeValue(forKey: room.name) }
        }
    }

    private func scheduleMarkRoomRead(_ room: String, generation: Int) {
        guard selection == "room", let last = messages.last?.id,
              last > (lastSeenByRoom[room] ?? 0) else { return }
        Task {
            try? await Task.sleep(nanoseconds: DesignTokens.unreadReadDelayNanoseconds)
            guard room == currentRoom, generation == roomGeneration, selection == "room", !roomLoading else { return }
            lastSeenByRoom[room] = max(lastSeenByRoom[room] ?? 0, last)
            UserDefaults.standard.set(lastSeenByRoom, forKey: "pack.lastSeen")
            unreadCounts.removeValue(forKey: room)
            unreadBoundary = nil
        }
    }

    // MARK: - derived

    var selectedTask: PackTask? { tasks.first { $0.id == selectedTaskId } }

    private func refreshAddrTokenStatus() {
        let status = AddressSource.status()
        addrTokenStatus = status == errSecSuccess ? "地址源令牌已存入钥匙串" : (status == errSecItemNotFound ? "地址源令牌未配置" : "地址源令牌状态无法读取")
    }

    func saveAddrToken() {
        let status = AddressSource.save(settingsAddrToken)
        settingsAddrToken = ""
        if status == errSecSuccess || status == errSecItemNotFound { refreshAddrTokenStatus() }
        else { addrTokenStatus = "地址源令牌保存失败（钥匙串状态 \(status)）" }
    }

    func saveHubURL() {
        if persistHubURL(settingsHubURL.trimmingCharacters(in: .whitespacesAndNewlines), manual: true) {
            Task { await refresh() }
        }
    }

    // Manual and automatic updates share the same atomic configuration write
    // and credential reload. Automatic failures leave settings silent.
    private func persistHubURL(_ text: String, manual: Bool) -> Bool {
        func fail(_ message: String) -> Bool {
            if manual { settingsMessage = message }
            return false
        }
        guard let parts = URLComponents(string: text), ["http", "https"].contains(parts.scheme?.lowercased() ?? ""),
              let host = parts.host, !host.isEmpty, parts.user == nil, parts.password == nil, parts.query == nil, parts.fragment == nil else {
            return fail("地址格式无效，请使用不带凭证或查询参数的 HTTP(S) 地址。")
        }
        let path = PackConfig.configurationURL
        do {
            let loaded = PackConfig.load()
            credentialStatus = loaded.credentialStatus
            guard var cfg = loaded.config else { return fail("无法读取本机凭证") }
            let data = try Data(contentsOf: path)
            guard var object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { return fail("配置格式无效") }
            object["hubUrl"] = text
            let updated = try JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys])
            let temporary = path.deletingLastPathComponent().appendingPathComponent(".config-\(UUID().uuidString).tmp")
            guard FileManager.default.createFile(atPath: temporary.path, contents: updated, attributes: [.posixPermissions: 0o600]) else { return fail("无法创建安全的配置临时文件") }
            defer { try? FileManager.default.removeItem(at: temporary) }
            _ = try FileManager.default.replaceItemAt(path, withItemAt: temporary)
            if manual { manualHubURLSaved = true }
            storedHubURL = text
            cfg.hubUrl = text
            installHubClients(cfg)
            settingsHubURL = text
            if manual { settingsMessage = "地址已保存，正在连接" }
            return true
        } catch { return fail("保存失败，请检查本机配置后重试") }
    }

    private func installHubClients(_ cfg: PackConfig) {
        clientGeneration += 1
        client = HubClient(cfg: cfg)
        if let token = cfg.ownerToken, !token.isEmpty {
            var ownerCfg = cfg; ownerCfg.token = token; ownerClient = HubClient(cfg: ownerCfg)
        } else { ownerClient = nil }
    }

    private func rebuildHubClients() -> Bool {
        let loaded = PackConfig.load()
        credentialStatus = loaded.credentialStatus
        guard var cfg = loaded.config else { return false }
        cfg.hubUrl = storedHubURL
        installHubClients(cfg)
        return true
    }

    private func followPublishedHubURL(rebuildOnFailure: Bool = false) async -> Bool {
        guard !discoveryInProgress, rebuildOnFailure || !manualHubURLSaved else { return false }
        if let host = URLComponents(string: storedHubURL)?.host?.lowercased(), ["127.0.0.1", "localhost", "::1", "[::1]"].contains(host) {
            return rebuildOnFailure && rebuildHubClients()
        }
        let now = ProcessInfo.processInfo.systemUptime
        if let last = lastHubDiscoveryUptime, now - last < hubDiscoveryInterval { return false }
        lastHubDiscoveryUptime = now
        discoveryInProgress = true
        defer { discoveryInProgress = false }
        if manualHubURLSaved { return rebuildOnFailure && rebuildHubClients() }
        let generation = clientGeneration
        if let value = await AddressSource.privateURL(timeout: hubDiscoveryTimeout) {
            guard generation == clientGeneration, !manualHubURLSaved else { return false }
            if value != storedHubURL, persistHubURL(value, manual: false) { return true }
            if value == storedHubURL { return rebuildOnFailure && rebuildHubClients() }
        }
        guard generation == clientGeneration, !manualHubURLSaved else { return false }
        guard let source = URL(string: "https://raw.githubusercontent.com/cyou1314/Pack/main/runtime/hub-url.txt") else {
            return rebuildOnFailure && rebuildHubClients()
        }
        var request = URLRequest(url: source, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: hubDiscoveryTimeout)
        request.setValue("no-cache", forHTTPHeaderField: "Cache-Control")
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            NSLog("地址源 raw.githubusercontent.com HTTP %d", (response as? HTTPURLResponse)?.statusCode ?? 0)
            // A manual save or another client change while the request was in flight wins.
            guard generation == clientGeneration, !manualHubURLSaved else { return false }
            if let http = response as? HTTPURLResponse, http.statusCode == 200,
               let value = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines),
               let parts = URLComponents(string: value), parts.scheme?.lowercased() == "https",
               let host = parts.host, !host.isEmpty, parts.user == nil, parts.password == nil,
               parts.query == nil, parts.fragment == nil, value != storedHubURL,
               persistHubURL(value, manual: false) { return true }
        } catch {
            guard generation == clientGeneration, !manualHubURLSaved else { return false }
        }
        return rebuildOnFailure && generation == clientGeneration && rebuildHubClients()
    }

    func toggleMember(_ name: String) {
        selectedMemberName = selectedMemberName == name ? nil : name
        selection = "members"
    }

    func loadMemberRunDurations(_ name: String, taskIds: [Int]) async {
        memberRunDurations = [:]; memberRunsFailed = []
        guard let client else { memberRunsFailed = Set(taskIds); memberRunsLoading = false; return }
        memberRunsLoading = true
        for id in taskIds {
            guard !Task.isCancelled, selectedMemberName == name else { return }
            do {
                let result = try await client.call(name: "task_audit", args: ["task_id": id])
                guard !Task.isCancelled, selectedMemberName == name else { return }
                let runs = (result["runs"] as? [[String: Any]]) ?? []
                if let run = runs.last(where: { ($0["member"] as? String) == name && ($0["status"] as? String) == "closed" && optDoubleVal($0["started_at"]) != nil && optDoubleVal($0["ended_at"]) != nil }),
                   let start = optDoubleVal(run["started_at"]), let end = optDoubleVal(run["ended_at"]), end >= start {
                    memberRunDurations[id] = end - start
                }
            } catch {
                guard !Task.isCancelled, selectedMemberName == name else { return }
                memberRunsFailed.insert(id)
            }
        }
        if selectedMemberName == name { memberRunsLoading = false }
    }

    func loadMemberSkills(_ name: String) async {
        memberSkills = []; memberSkillsStatus = "正在读取技能…"
        guard let client else { memberSkillsStatus = "技能读取失败：中枢未连接"; return }
        do {
            let response = try await client.call(name: "skill_list")
            guard !Task.isCancelled, selectedMemberName == name else { return }
            guard let skills = response["skills"] as? [[String: Any]], skills.allSatisfy({ $0["assignees"] is [[String: Any]] }) else {
                memberSkillsStatus = "技能读取失败：中枢未返回指派信息"; return
            }
            memberSkills = skills.compactMap { skill in
                guard let assignments = skill["assignees"] as? [[String: Any]],
                      assignments.contains(where: { ($0["member"] as? String) == name && boolVal($0["enabled"]) }),
                      let skillName = skill["name"] as? String else { return nil }
                let version = skill["version"].map { String(describing: $0) } ?? "未知版本"
                return AssignedSkill(name: skillName, version: version)
            }
            memberSkillsStatus = memberSkills.isEmpty ? "暂无已启用技能" : ""
        } catch {
            if !Task.isCancelled && selectedMemberName == name { memberSkillsStatus = "技能读取失败，请稍后重试" }
        }
    }

    func openAvatarPicker(_ member: MemberInfo) {
        avatarTargetName = member.name
        editAvatarSpecies = member.avatar?.species ?? "fox"
        editAvatarPalette = member.avatar?.palette ?? "orange"
        editAvatarAccessory = member.avatar?.accessory ?? "none"
        editAvatarBackground = member.avatar?.bg ?? "cream"
        avatarSaveError = ""
        showAvatarPicker = true
    }

    func restoreDesignedAvatar() {
        guard !avatarSaving else { return }
        var customNames = Set(UserDefaults.standard.stringArray(forKey: "pack.customAvatarNames") ?? [])
        customNames.remove(avatarTargetName)
        UserDefaults.standard.set(Array(customNames), forKey: "pack.customAvatarNames")
        avatarRevision += 1
        showAvatarPicker = false
    }

    func saveMemberAvatar() {
        guard !avatarSaving, let client else { return }
        let name = avatarTargetName
        let combo = ["species": editAvatarSpecies, "palette": editAvatarPalette, "accessory": editAvatarAccessory, "bg": editAvatarBackground]
        guard let bytes = try? JSONSerialization.data(withJSONObject: combo, options: [.sortedKeys]) else { avatarSaveError = "头像组合无法编码"; return }
        avatarSaving = true; avatarSaveError = ""
        Task {
            defer { avatarSaving = false }
            do {
                _ = try await client.call(name: "member_avatar_set", args: ["name": name, "avatar_combo": String(decoding: bytes, as: UTF8.self)])
                var customNames = Set(UserDefaults.standard.stringArray(forKey: "pack.customAvatarNames") ?? [])
                customNames.insert(name)
                UserDefaults.standard.set(Array(customNames), forKey: "pack.customAvatarNames")
                avatarRevision += 1
                showAvatarPicker = false
                await refresh()
            } catch let error as HubError {
                switch error {
                case .toolError(let message), .badResponse(let message): avatarSaveError = message
                }
            } catch { avatarSaveError = error.localizedDescription }
        }
    }

    func memberStatus(_ member: MemberInfo) -> String {
        if let login = loginStatus(member.loginState) { return login }
        if member.quotaExhausted { return "额度耗尽" }
        if let task = tasks.first(where: { $0.assignee == member.name && ["claimed", "working"].contains($0.status) }) { return "执行中 · #\(task.id)" }
        if member.isOnline { return "在线" }
        if pendingMembers.contains(member.name) && member.lastHeartbeat == nil { return "接入中 · 等待登录" }
        return "离线"
    }

    func loginStatus(_ value: String?) -> String? {
        switch value {
        case "pending": return "接入中 · 等待登录"
        case "logged_in": return "已登录 · 待引擎接入"
        case "expired": return "登录已失效 · 待重新登录"
        default: return nil
        }
    }

    func openMemberLogin(_ member: MemberInfo) {
        loginTargetName = member.name
        loginTargetProvider = member.provider
        memberActionError = ""
        showMemberLogin = true
    }

    func completeMemberLogin() {
        guard !memberLoginSaving else { return }
        guard let client else { memberActionError = "中枢未连接，登录状态未保存"; return }
        let name = loginTargetName
        memberLoginSaving = true
        memberActionError = ""
        Task {
            defer { memberLoginSaving = false }
            do {
                _ = try await client.call(name: "member_login_state_set", args: ["name": name, "state": "logged_in"])
                showMemberLogin = false
                await refresh()
            } catch let error as HubError {
                switch error {
                case .toolError(let message), .badResponse(let message): memberActionError = "登录状态保存失败：\(message)"
                }
            } catch { memberActionError = "登录状态保存失败：\(error.localizedDescription)" }
        }
    }

    private func saveHandoffResult(_ name: String, _ result: String) {
        memberHandoffResults[name] = "\(Date().formatted()) · \(result)"
        UserDefaults.standard.set(memberHandoffResults, forKey: "pack.memberHandoffResults")
    }

    private func applyEngineState(_ state: String, member: String) async -> String {
        guard state == "needs_login" else { return state == "not_found" ? "引擎无此账号" : "引擎状态：\(state)" }
        guard let client else { return "引擎会话已失效；中枢未连接，失效状态未写回" }
        do {
            _ = try await client.call(name: "member_login_state_set", args: ["name": member, "state": "expired"])
            await refresh()
            return "引擎会话已失效，已标记待重新登录"
        } catch { return "引擎会话已失效，但中枢状态写回失败：\(error.localizedDescription)" }
    }

    func checkMemberEngine(_ member: MemberInfo) {
        guard memberHandoffInProgress == nil else { return }
        memberHandoffInProgress = member.name
        Task {
            defer { memberHandoffInProgress = nil }
            do { saveHandoffResult(member.name, await applyEngineState(try await MemberHandoff.operation("status", member: member.name), member: member.name)) }
            catch { saveHandoffResult(member.name, "状态查询失败：\(error.localizedDescription)") }
        }
    }

    func handoffMember(_ member: MemberInfo) {
        guard memberHandoffInProgress == nil, member.loginState == "logged_in" else { return }
        memberHandoffInProgress = member.name
        Task {
            defer { memberHandoffInProgress = nil }
            do {
                let (result, state) = try await MemberHandoff.perform(member: member.name)
                let suffix = state == "needs_login" ? " · " + (await applyEngineState(state, member: member.name)) : ""
                saveHandoffResult(member.name, result + suffix)
            } catch { saveHandoffResult(member.name, "交接失败：\(error.localizedDescription)") }
        }
    }

    func registerMember() {
        guard let client else { memberActionError = "中枢未连接"; return }
        let name = memberNameInput.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { memberActionError = "请输入成员名称"; return }
        Task {
            do {
                let combo = ["species": avatarSpecies, "palette": avatarPalette, "accessory": avatarAccessory, "bg": avatarBackground]
                let data = try JSONSerialization.data(withJSONObject: combo, options: [.sortedKeys])
                let response = try await client.call(name: "member_register", args: ["name": name, "avatar_combo": String(decoding: data, as: UTF8.self), "provider": "muse"])
                guard let token = response["token"] as? String, let bytes = token.data(using: .utf8) else { memberActionError = "登记结果缺少凭证，请联系调度员处理"; return }
                let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: "com.cyou.pack.member", kSecAttrAccount as String: name, kSecValueData as String: bytes, kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly]
                let result = SecItemAdd(query as CFDictionary, nil)
                guard result == errSecSuccess else { memberActionError = "成员已登记，但凭证未能存入钥匙串；请联系调度员重置"; return }
                if !pendingMembers.contains(name) { pendingMembers.append(name); UserDefaults.standard.set(pendingMembers, forKey: "pack.pendingMembers") }
                showAddMember = false; memberNameInput = ""; selectedMemberName = name; selection = "members"
                await refresh()
            } catch { memberActionError = "登记未完成，请检查名称和头像组合后重试" }
        }
    }

    func removeSelectedMember() {
        guard !memberRemovalInProgress, let name = selectedMemberName, name != "owner", name != "dispatcher", let client else { return }
        memberRemovalInProgress = true
        let isProviderMember = members.first(where: { $0.name == name })?.provider == "muse"
        Task {
            defer { memberRemovalInProgress = false }
            var lines: [String] = []
            do { lines.append(try await MemberLoginWebView.removeStore(for: name) ? "登录分区：已清除" : "登录分区：无记录") }
            catch { lines.append("登录分区：清除失败（\(error.localizedDescription)）") }
            if isProviderMember {
                do {
                    let status = try await MemberHandoff.operation("remove", member: name)
                    lines.append(status == "not_found" ? "引擎账号与分区：无此账号" : "引擎账号与分区：已清除")
                } catch { lines.append("引擎账号与分区：清除失败（\(error.localizedDescription)）") }
            } else { lines.append("引擎账号与分区：非登录容器成员，未操作") }
            do {
                _ = try await client.call(name: "member_remove", args: ["name": name])
                lines.append("中枢成员：已移除")
                SecItemDelete([kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: "com.cyou.pack.member", kSecAttrAccount as String: name] as CFDictionary)
                pendingMembers.removeAll { $0 == name }; UserDefaults.standard.set(pendingMembers, forKey: "pack.pendingMembers")
                selectedMemberName = nil; await refresh()
            } catch { lines.append("中枢成员：移除失败（\(error.localizedDescription)）") }
            memberRemovalReport = "\(name)\n" + lines.joined(separator: "\n")
            UserDefaults.standard.set(memberRemovalReport, forKey: "pack.memberRemovalReport")
        }
    }

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

    func selectRoom(_ room: String) {
        guard room != currentRoom else { selection = "room"; return }
        if let last = messages.last?.id { lastSeenByRoom[currentRoom] = last; UserDefaults.standard.set(lastSeenByRoom, forKey: "pack.lastSeen") }
        currentRoom = room
        roomGeneration += 1
        roomLoading = true
        roomSummary = nil
        unreadBoundary = lastSeenByRoom[room]
        draftMessage = roomDrafts[room] ?? ""
        messages = []; tasks = []; selectedTaskId = nil; highlightedMessageId = nil
        expandedChatTaskId = nil; mentionDismissedText = nil; selection = "room"
        Task { await refresh() }
    }

    func openRoomDialog(rename: String? = nil) {
        roomRenameFrom = rename; roomDialogName = rename ?? ""; roomDialogError = ""; showRoomDialog = true
    }

    func saveRoom() {
        guard let client else { roomDialogError = "中枢未连接"; return }
        let name = roomDialogName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty, name.count <= 40, !name.contains(where: { $0.isWhitespace || $0 == "/" || $0 == "\\" }) else {
            roomDialogError = "名称须为 1–40 字，不能包含空格或斜杠"; return
        }
        let old = roomRenameFrom
        Task {
            do {
                if let old {
                    _ = try await client.call(name: "room_rename", args: ["room": old, "new_name": name])
                    if let draft = roomDrafts.removeValue(forKey: old) { roomDrafts[name] = draft; UserDefaults.standard.set(roomDrafts, forKey: "pack.roomDrafts") }
                    if currentRoom == old { currentRoom = name }
                } else { _ = try await client.call(name: "room_create", args: ["name": name]) }
                showRoomDialog = false
                if currentRoom != name { selectRoom(name) } else { await refresh() }
            } catch { roomDialogError = "操作未完成，请检查连接后重试" }
        }
    }

    func archiveRoom(_ name: String) {
        guard name != "pack-main", let client else { return }
        Task {
            do {
                _ = try await client.call(name: "room_archive", args: ["room": name, "archived": true])
                if currentRoom == name { selectRoom("pack-main") } else { await refresh() }
            } catch { lastActionNote = "操作未完成，请检查连接后重试" }
        }
    }

    /// Range of the trailing token of the draft when it is an unfinished
    /// mention: the token starts at the beginning of the text or right
    /// after a space/newline and its first character is @ or ＠.
    /// (v1 assumes the cursor is at the end of the text while typing.)
    private var mentionTokenRange: Range<String.Index>? {
        let text = draftMessage
        guard !text.isEmpty else { return nil }
        var start = text.startIndex
        if let ws = text.lastIndex(where: { $0 == " " || $0 == "\n" }) {
            start = text.index(after: ws)
        }
        guard start < text.endIndex else { return nil }
        let first = text[start]
        guard first == "@" || first == "＠" else { return nil }
        return start..<text.endIndex
    }

    /// The text after the @ of the unfinished mention, nil when the draft
    /// does not end in a mention token.
    var mentionQuery: String? {
        guard let r = mentionTokenRange else { return nil }
        return String(draftMessage[r].dropFirst())
    }

    /// Members the popup offers: assignable members (same role filter as
    /// the 派活 picker) minus the App's own chat identity "owner",
    /// prefix-filtered by the query, case-insensitive.
    var mentionCandidates: [MemberInfo] {
        guard let q = mentionQuery?.lowercased() else { return [] }
        return members.filter { m in
            m.role == "member" && m.name != "owner" &&
            (q.isEmpty || m.name.lowercased().hasPrefix(q))
        }.sorted { a, b in
            let aAvailable = a.isOnline && !a.quotaExhausted
            let bAvailable = b.isOnline && !b.quotaExhausted
            return aAvailable == bAvailable ? a.name < b.name : aAvailable && !bAvailable
        }
    }

    func mentionStatus(_ member: MemberInfo) -> String {
        if member.quotaExhausted { return "额度耗尽" }
        if !member.isOnline { return pendingMembers.contains(member.name) && member.lastHeartbeat == nil ? "接入中" : "离线" }
        return memberStatus(member)
    }

    var mentionPopupVisible: Bool {
        mentionQuery != nil && !mentionCandidates.isEmpty &&
        draftMessage != mentionDismissedText
    }

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

    // MARK: - actions

    private func act(_ note: String, _ op: @escaping (HubClient) async throws -> [String: Any]) {
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

    func addAttachments(_ urls: [URL]) {
        let room = currentRoom
        guard let ownerClient else { attachmentNoticeByRoom[room] = "缺少 owner 凭证，无法上传图片。"; return }
        let available = max(0, AttachmentLimits.maxCount - (attachmentsByRoom[room]?.count ?? 0))
        guard available > 0 else { attachmentNoticeByRoom[room] = "每条消息最多添加 4 张图片。"; return }
        attachmentNoticeByRoom[room] = urls.count > available ? "每条消息最多添加 4 张图片，多余图片未添加。" : nil
        for url in urls.prefix(available) {
            let item = PendingAttachment(id: UUID(), url: url)
            attachmentsByRoom[room, default: []].append(item)
            uploadAttachment(item.id, room: room, client: ownerClient)
        }
    }

    func removeAttachment(_ id: UUID) {
        attachmentsByRoom[currentRoom]?.removeAll { $0.id == id }
    }

    func retryAttachment(_ id: UUID) {
        guard let ownerClient else { attachmentNoticeByRoom[currentRoom] = "缺少 owner 凭证，无法上传图片。"; return }
        let room = currentRoom
        guard let index = attachmentsByRoom[room]?.firstIndex(where: { $0.id == id }) else { return }
        attachmentsByRoom[room]?[index].uploading = true
        attachmentsByRoom[room]?[index].error = nil
        uploadAttachment(id, room: room, client: ownerClient)
    }

    private func uploadAttachment(_ id: UUID, room: String, client: HubClient) {
        guard let url = attachmentsByRoom[room]?.first(where: { $0.id == id })?.url else { return }
        Task {
            do {
                let prepared = try await Task.detached { try AttachmentImage.prepare(url) }.value
                guard let previewIndex = attachmentsByRoom[room]?.firstIndex(where: { $0.id == id }) else { return }
                attachmentsByRoom[room]?[previewIndex].preview = NSImage(data: prepared.data)
                let result = try await client.call(name: "attachment_put", args: ["room": room, "filename": prepared.filename,
                    "mime": prepared.mime, "data_base64": prepared.data.base64EncodedString()])
                guard let attachmentID = optIntVal(result["attachment_id"]), attachmentID > 0 else { throw HubError.badResponse("上传结果缺少附件编号") }
                guard let index = attachmentsByRoom[room]?.firstIndex(where: { $0.id == id }) else { return }
                attachmentsByRoom[room]?[index].uploadedID = attachmentID
                attachmentsByRoom[room]?[index].preview = NSImage(data: prepared.data)
                attachmentsByRoom[room]?[index].uploading = false
            } catch {
                guard let index = attachmentsByRoom[room]?.firstIndex(where: { $0.id == id }) else { return }
                attachmentsByRoom[room]?[index].uploading = false
                if let error = error as? HubError {
                    switch error { case .badResponse(let message), .toolError(let message): attachmentsByRoom[room]?[index].error = message }
                } else { attachmentsByRoom[room]?[index].error = error.localizedDescription }
            }
        }
    }

    func loadMessageAttachment(_ id: Int, retry: Bool = false) async {
        if retry { attachmentFailures.remove(id) }
        guard attachmentImages[id] == nil, !attachmentFailures.contains(id), !attachmentLoading.contains(id) else { return }
        guard let client else { attachmentFailures.insert(id); return }
        attachmentLoading.insert(id)
        defer { attachmentLoading.remove(id) }
        do {
            let result = try await client.call(name: "attachment_get", args: ["attachment_id": id])
            guard let encoded = result["data_base64"] as? String, let data = Data(base64Encoded: encoded),
                  data.count <= AttachmentLimits.maxBytes, let image = NSImage(data: data) else { throw HubError.badResponse("图片数据不可用") }
            if attachmentImages.count >= AttachmentLimits.imageCacheCount, let key = attachmentImages.keys.first { attachmentImages.removeValue(forKey: key) }
            attachmentImages[id] = image
        } catch { attachmentFailures.insert(id) }
    }

    func sendDraft() {
        let room = currentRoom
        let selected = attachmentsByRoom[room] ?? []
        guard sendingMessage == nil else { return }
        guard selected.allSatisfy({ !$0.uploading && $0.uploadedID != nil }) else {
            attachmentNoticeByRoom[room] = "请等待图片上传，或移除／重试上传失败的图片。"; return
        }
        let body = draftMessage.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !body.isEmpty || !selected.isEmpty else { return }
        guard let ownerClient else { lastActionNote = "操作失败：缺少 owner 凭证"; return }
        let reply = replyByRoom.removeValue(forKey: room)
        let outgoing = FailedChatMessage(id: UUID(), room: room, body: body,
                                         wireBody: ReplyCodec.encode(body, reply: reply),
                                         reply: reply, attachments: selected)
        let selectedIDs = Set(selected.map(\.id))
        attachmentsByRoom[room]?.removeAll { selectedIDs.contains($0.id) }
        attachmentNoticeByRoom[room] = nil
        draftMessage = ""
        sendChat(outgoing, client: ownerClient, retry: false)
    }

    func retryFailedMessage(_ message: FailedChatMessage) {
        guard sendingMessage == nil, let ownerClient,
              failedMessagesByRoom[message.room]?.contains(where: { $0.id == message.id }) == true else { return }
        failedMessageNotices[message.id] = nil
        sendChat(message, client: ownerClient, retry: true)
    }

    private func sendChat(_ message: FailedChatMessage, client: HubClient, retry: Bool) {
        sendingRoom = message.room
        sendingMessage = message.body
        sendingReply = message.reply
        sendingFailedID = retry ? message.id : nil
        Task {
            do {
                await client.discoverBeforeSending()
                var args: [String: Any] = ["room": message.room, "body": message.wireBody, "kind": "chat"]
                let ids = message.attachments.compactMap(\.uploadedID)
                if !ids.isEmpty {
                    let json = try JSONSerialization.data(withJSONObject: ids)
                    args["attachment_ids"] = String(decoding: json, as: UTF8.self)
                }
                _ = try await client.call(name: "room_post", args: args)
                failedMessagesByRoom[message.room]?.removeAll { $0.id == message.id }
                failedMessageNotices[message.id] = nil
                sendingReply = nil; sendingMessage = nil; sendingRoom = nil; sendingFailedID = nil
                lastActionNote = "已发送"
                await refresh()
            } catch {
                if failedMessagesByRoom[message.room]?.contains(where: { $0.id == message.id }) != true {
                    failedMessagesByRoom[message.room, default: []].append(message)
                }
                sendingReply = nil; sendingMessage = nil; sendingRoom = nil; sendingFailedID = nil
                lastActionNote = "操作未完成，请检查连接后重试"
            }
        }
    }

    func discardFailedMessage(_ message: FailedChatMessage) {
        guard sendingFailedID != message.id,
              failedMessagesByRoom[message.room]?.contains(where: { $0.id == message.id }) == true else { return }
        let existingReply = replyByRoom[message.room]
        if let reply = message.reply, let existingReply, existingReply != reply {
            failedMessageNotices[message.id] = "当前草稿已有其他引用，请先处理引用再放弃此消息。"; return
        }
        let existingAttachments = attachmentsByRoom[message.room] ?? []
        guard existingAttachments.count + message.attachments.count <= AttachmentLimits.maxCount else {
            failedMessageNotices[message.id] = "当前草稿图片数量已满，请先移除图片再放弃此消息。"; return
        }
        let existingText = message.room == currentRoom ? draftMessage : (roomDrafts[message.room] ?? "")
        let restored = [existingText, message.body].filter { !$0.isEmpty }.joined(separator: "\n")
        if message.room == currentRoom { draftMessage = restored }
        else { roomDrafts[message.room] = restored; UserDefaults.standard.set(roomDrafts, forKey: "pack.roomDrafts") }
        if let reply = message.reply, existingReply == nil { replyByRoom[message.room] = reply }
        attachmentsByRoom[message.room] = existingAttachments + message.attachments
        failedMessagesByRoom[message.room]?.removeAll { $0.id == message.id }
        failedMessageNotices[message.id] = nil
    }

    func replyTo(_ message: RoomMessage) {
        let visible = message.deleted ? "（已删除）" : ReplyCodec.decode(message.body).body
        let snapshot = String(visible.prefix(DesignTokens.replySnapshotLimit))
        replyByRoom[currentRoom] = ReplySnapshot(author: message.author, text: snapshot)
        selection = "room"
        composerFocusRequest += 1
    }

    func editMessage(_ message: RoomMessage) {
        guard message.author == "owner", message.kind == "chat", !message.deleted else { return }
        editingMessageId = message.id
        editingMessageText = ReplyCodec.decode(message.body).body
    }

    func saveMessageEdit(_ message: RoomMessage) {
        let text = editingMessageText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard message.author == "owner", message.kind == "chat", !message.deleted, !text.isEmpty, let ownerClient else { return }
        let wireText = ReplyCodec.encode(text, reply: ReplyCodec.decode(message.body).reply)
        Task {
            do {
                _ = try await ownerClient.call(name: "message_edit", args: ["message_id": message.id, "new_body": wireText])
                editingMessageId = nil; editingMessageText = ""
                await refresh()
            } catch { lastActionNote = "操作未完成，请检查连接后重试" }
        }
    }

    func deleteMessage(_ message: RoomMessage) {
        guard message.author == "owner", message.kind == "chat", !message.deleted, let ownerClient else { return }
        Task {
            do {
                _ = try await ownerClient.call(name: "message_delete", args: ["message_id": message.id])
                await refresh()
            } catch { lastActionNote = "操作未完成，请检查连接后重试" }
        }
    }

    /// Replace the unfinished @… token with "@成员名 " — always the
    /// half-width @ and the member's canonical hub name, because the
    /// room watcher turns plain-text @全名 into task cards.
    func completeMention(_ member: MemberInfo) {
        guard let r = mentionTokenRange else { return }
        draftMessage.replaceSubrange(r, with: "@\(member.name) ")
        mentionDismissedText = nil
        composerFocusRequest += 1
    }

    func dismissMentions() {
        mentionDismissedText = draftMessage
    }

    func submitComposer() {
        if showInlineTask {
            guard replyByRoom[currentRoom] == nil else { inlineTaskError = "回复消息请先关闭派活"; return }
            createTaskFromDraft()
        } else { sendDraft() }
    }

    private func createTaskFromDraft() {
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
    func approveAlways(_ task: PackTask, requestID: Int) {
        decideTask(task.id, approve: true, always: (requestID, task.title, task.assignee))
    }
    func rejectTask(_ tid: Int) { decideTask(tid, approve: false) }

    private func decideTask(_ tid: Int, approve: Bool, always: (requestID: Int, title: String, assignee: String?)? = nil) {
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
                    if let target = always.assignee?.trimmingCharacters(in: .whitespacesAndNewlines), !target.isEmpty {
                        let pattern = try? NSRegularExpression(pattern: #"\[[A-Za-z0-9_-]+\]"#)
                        let range = NSRange(always.title.startIndex..., in: always.title)
                        let match = pattern?.firstMatch(in: always.title, range: range)
                        let action = match.flatMap { Range($0.range, in: always.title) }.map { String(always.title[$0]) } ?? ""
                        do {
                            _ = try await client.call(name: "rule_upsert", args: ["action": action, "target": target, "effect": "allow", "source": "app-always"])
                            alwaysRuleSucceeded.insert(always.requestID)
                        } catch {
                            alwaysRuleErrors[always.requestID] = "本次已批准，但总是允许规则保存未确认，请检查规则后重试。"
                        }
                    } else {
                        alwaysRuleErrors[always.requestID] = "本次已批准，但任务没有指派成员，未保存总是允许规则。"
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

@main
struct PackApp: App {
    var body: some Scene {
        WindowGroup {
            ContentView()
        }.defaultSize(width: 1280, height: 820)
    }
}
