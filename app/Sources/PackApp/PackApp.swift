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
    @Published var attachments: [URL] = []
    @Published var artifactPreviewURL: URL?
    @Published var showArtifactPreview = false
    @Published var selectedArtifacts: [Int: Int] = [:]
    @Published var showMemberLogin = false
    @Published var showRemoveMember = false
    @Published var pendingMembers: [String] = UserDefaults.standard.stringArray(forKey: "pack.pendingMembers") ?? []
    @Published var draftMessage: String = "" {
        didSet {
            roomDrafts[currentRoom] = draftMessage
            UserDefaults.standard.set(roomDrafts, forKey: "pack.roomDrafts")
        }
    }
    @Published var sendingMessage: String?
    @Published var sendingRoom: String?
    /// Draft text at the moment the user dismissed the @-mention popup
    /// (Esc / click elsewhere); the popup stays hidden while the draft
    /// still equals this, and reappears as soon as the text changes.
    @Published var mentionDismissedText: String?
    @Published var showCreateTask: Bool = false
    @Published var showInlineTask: Bool = false
    @Published var showSidebar: Bool = true
    @Published var sidebarPinned: Bool = false
    @Published var roomExpanded: Bool = true
    @Published var newTitle: String = ""
    @Published var newBody: String = ""
    @Published var newAssignee: String = "absent"
    @Published var newNeedsApproval: Bool = false
    @Published var rejectReasons: [Int: String] = [:]
    @Published var lastActionNote: String = ""
    @Published var settingsHubURL = ""
    @Published var settingsMessage = ""
    @Published var hasLocalCredentials = false
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
    private var roomGeneration = 0
    private var unreadTask: Task<Void, Never>?

    func start() {
        guard pollTask == nil else { return }
        draftMessage = roomDrafts[currentRoom] ?? ""
        newNeedsApproval = approvalDefault
        guard let cfg = PackConfig.load() else {
            connectionNote = "缺少配置 ~/.pack/config.json"
            roomLoading = false
            showOnboarding = true
            return
        }
        settingsHubURL = cfg.hubUrl
        hasLocalCredentials = true
        client = HubClient(cfg: cfg)
        if let ownerToken = cfg.ownerToken, !ownerToken.isEmpty {
            var ownerCfg = cfg
            ownerCfg.token = ownerToken
            ownerClient = HubClient(cfg: ownerCfg)
        }
        pollTask = Task { [weak self] in
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
        do {
            let roomRes = try await client.call(name: "room_list")
            let msgRes = try await client.call(name: "room_read_new",
                                               args: ["room": room, "since_id": 0, "limit": 200])
            let taskRes = try await client.call(name: "task_list")
            let memRes = try await client.call(name: "member_list")
            let summaryRes = try? await client.call(name: "room_summary_get", args: ["room": room])
            guard room == currentRoom, generation == roomGeneration else { return }
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
                messages = arr.map {
                    RoomMessage(id: intVal($0["id"]), author: ($0["author"] as? String) ?? "?",
                                kind: ($0["kind"] as? String) ?? "chat", body: ($0["body"] as? String) ?? "",
                                taskId: optIntVal($0["task_id"]), createdAt: doubleVal($0["created_at"]),
                                deleted: boolVal($0["deleted"]), edited: boolVal($0["edited"]))
                }
            }
            if let arr = taskRes["tasks"] as? [[String: Any]] {
                let roomTaskIds = Set(messages.compactMap(\.taskId))
                tasks = arr.filter { roomTaskIds.contains(intVal($0["id"])) }.map {
                    PackTask(id: intVal($0["id"]), title: ($0["title"] as? String) ?? "",
                             body: ($0["body"] as? String) ?? "", status: ($0["status"] as? String) ?? "",
                             assignee: optStringVal($0["assignee"]), attempts: intVal($0["attempts"]),
                             result: optStringVal($0["result"]), leaseExpires: optDoubleVal($0["lease_expires"]),
                             updatedAt: doubleVal($0["updated_at"]),
                             envelope: envelopeVal($0["envelope"]),
                             failClass: optStringVal($0["fail_class"]),
                             nextClaimAt: optDoubleVal($0["next_claim_at"]),
                             schedAt: optDoubleVal($0["sched_at"]),
                             createdBy: optStringVal($0["created_by"]), reviewNote: optStringVal($0["review_note"]))
                }
            }
            if let arr = memRes["members"] as? [[String: Any]] {
                members = arr.map {
                    MemberInfo(name: ($0["name"] as? String) ?? "?", role: ($0["role"] as? String) ?? "",
                               profile: optStringVal($0["profile"]),
                               avatar: avatarVal($0["avatar"]),
                               lastHeartbeat: optDoubleVal($0["last_heartbeat"]),
                               quotaWeekUsedPct: optDoubleVal($0["quota_week_used_pct"]),
                               quotaWeekResetAt: optDoubleVal($0["quota_week_reset_at"]),
                               quotaExtraLeft: optIntVal($0["quota_extra_left"]),
                               quotaUpdatedAt: optDoubleVal($0["quota_updated_at"]))
                }
            }
            roomLoading = false
            connected = true
            connectionFailed = false
            connectionNote = "已连接"
            let snapshot = rooms
            if unreadTask == nil {
                unreadTask = Task { [weak self] in
                    await self?.refreshUnreadCounts(snapshot)
                    self?.unreadTask = nil
                }
            }
            scheduleMarkRoomRead(room, generation: generation)
        } catch {
            guard room == currentRoom, generation == roomGeneration else { return }
            roomLoading = false
            connected = false
            connectionFailed = true
            connectionNote = "连接中断，重试中"
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

    func saveHubURL() {
        let text = settingsHubURL.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let parts = URLComponents(string: text), ["http", "https"].contains(parts.scheme?.lowercased() ?? ""), parts.host != nil, parts.user == nil, parts.password == nil, parts.query == nil, parts.fragment == nil else {
            settingsMessage = "地址格式无效，请使用不带凭证或查询参数的 HTTP(S) 地址。"; return
        }
        let path = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".pack/config.json")
        do {
            let data = try Data(contentsOf: path)
            guard var object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { settingsMessage = "配置格式无效"; return }
            object["hubUrl"] = text
            let updated = try JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys])
            let temporary = path.deletingLastPathComponent().appendingPathComponent(".config-\(UUID().uuidString).tmp")
            guard FileManager.default.createFile(atPath: temporary.path, contents: updated, attributes: [.posixPermissions: 0o600]) else { settingsMessage = "无法创建安全的配置临时文件"; return }
            _ = try FileManager.default.replaceItemAt(path, withItemAt: temporary)
            guard let cfg = PackConfig.load() else { settingsMessage = "保存后无法读取配置"; return }
            client = HubClient(cfg: cfg)
            if let token = cfg.ownerToken, !token.isEmpty { var ownerCfg = cfg; ownerCfg.token = token; ownerClient = HubClient(cfg: ownerCfg) }
            settingsMessage = "地址已保存，正在连接"
            Task { await refresh() }
        } catch { settingsMessage = "保存失败，请检查本机配置后重试" }
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

    func memberStatus(_ member: MemberInfo) -> String {
        if member.quotaExhausted { return "额度耗尽" }
        if let task = tasks.first(where: { $0.assignee == member.name && ["claimed", "working"].contains($0.status) }) { return "执行中 · #\(task.id)" }
        if member.isOnline { return "在线" }
        if pendingMembers.contains(member.name) && member.lastHeartbeat == nil { return "接入中 · 等待登录" }
        return "离线"
    }

    func registerMember() {
        guard let client else { memberActionError = "中枢未连接"; return }
        let name = memberNameInput.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { memberActionError = "请输入成员名称"; return }
        Task {
            do {
                let combo = ["species": avatarSpecies, "palette": avatarPalette, "accessory": avatarAccessory, "bg": avatarBackground]
                let data = try JSONSerialization.data(withJSONObject: combo, options: [.sortedKeys])
                let response = try await client.call(name: "member_register", args: ["name": name, "avatar_combo": String(decoding: data, as: UTF8.self)])
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
        guard let name = selectedMemberName, name != "owner", name != "dispatcher", let client else { return }
        Task {
            do {
                _ = try await client.call(name: "member_remove", args: ["name": name])
                SecItemDelete([kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: "com.cyou.pack.member", kSecAttrAccount as String: name] as CFDictionary)
                pendingMembers.removeAll { $0 == name }; UserDefaults.standard.set(pendingMembers, forKey: "pack.pendingMembers")
                selectedMemberName = nil; await refresh()
            } catch { memberActionError = "移除未完成，请检查连接后重试" }
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
        }
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

    func sendDraft() {
        guard attachments.isEmpty else { lastActionNote = "中枢不支持附件上传，请先移除所选文件"; return }
        let body = draftMessage.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !body.isEmpty else { return }
        guard let ownerClient else {
            lastActionNote = "操作失败：缺少 owner 凭证"
            return
        }
        let room = currentRoom
        draftMessage = ""
        sendingRoom = room
        sendingMessage = body
        Task {
            do {
                await ownerClient.discoverBeforeSending()
                _ = try await ownerClient.call(name: "room_post", args: ["room": room, "body": body, "kind": "chat"])
                sendingMessage = nil
                sendingRoom = nil
                lastActionNote = "已发送"
                await refresh()
            } catch {
                sendingMessage = nil
                sendingRoom = nil
                if currentRoom == room && draftMessage.isEmpty { draftMessage = body }
                else if (roomDrafts[room] ?? "").isEmpty { roomDrafts[room] = body; UserDefaults.standard.set(roomDrafts, forKey: "pack.roomDrafts") }
                lastActionNote = "操作未完成，请检查连接后重试"
            }
        }
    }

    func editMessage(_ message: RoomMessage) {
        guard message.author == "owner", message.kind == "chat", !message.deleted else { return }
        editingMessageId = message.id
        editingMessageText = message.body
    }

    func saveMessageEdit(_ message: RoomMessage) {
        let text = editingMessageText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard message.author == "owner", message.kind == "chat", !message.deleted, !text.isEmpty, let ownerClient else { return }
        Task {
            do {
                _ = try await ownerClient.call(name: "message_edit", args: ["message_id": message.id, "new_body": text])
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
    }

    func dismissMentions() {
        mentionDismissedText = draftMessage
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

    func approveTask(_ tid: Int) {
        let room = currentRoom
        act("已批准任务 #\(tid)") { c in
            _ = try await c.call(name: "room_post",
                                 args: ["room": room, "body": "批准 任务#\(tid)（群主已在 App 批准）", "kind": "decision"])
            return try await c.call(name: "room_read_new", args: ["room": room, "since_id": 0, "limit": 1])
        }
    }

    func rejectTask(_ tid: Int) {
        let room = currentRoom
        let reason = (rejectReasons[tid] ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let suffix = reason.isEmpty ? "" : "：\(reason)"
        act("已拒绝并取消任务 #\(tid)") { c in
            _ = try await c.call(name: "room_post",
                                  args: ["room": room, "body": "拒绝 任务#\(tid)\(suffix)", "kind": "decision"])
            return try await c.call(name: "task_cancel", args: ["task_id": tid])
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
