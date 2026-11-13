import SwiftUI

@MainActor
final class AppState: ObservableObject {
    @Published var messages: [RoomMessage] = []
    @Published var tasks: [PackTask] = []
    @Published var members: [MemberInfo] = []
    @Published var connectionNote: String = "未连接"
    @Published var connected: Bool = false
    @Published var selection: String? = "room"
    @Published var selectedTaskId: Int?
    @Published var draftMessage: String = ""
    @Published var showCreateTask: Bool = false
    @Published var newTitle: String = ""
    @Published var newBody: String = ""
    @Published var newAssignee: String = "absent"
    @Published var newNeedsApproval: Bool = false
    @Published var rejectReasons: [Int: String] = [:]
    @Published var lastActionNote: String = ""

    private var client: HubClient?
    private var pollTask: Task<Void, Never>?

    func start() {
        guard pollTask == nil else { return }
        guard let cfg = PackConfig.load() else {
            connectionNote = "缺少配置 ~/.pack/config.json"
            return
        }
        client = HubClient(cfg: cfg)
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.refresh()
                try? await Task.sleep(nanoseconds: 15_000_000_000)
            }
        }
    }

    func refresh() async {
        guard let client else { return }
        do {
            let msgRes = try await client.call(name: "room_read_new",
                                               args: ["room": "pack-main", "since_id": 0, "limit": 200])
            let taskRes = try await client.call(name: "task_list")
            let memRes = try await client.call(name: "member_list")
            if let arr = msgRes["messages"] as? [[String: Any]] {
                messages = arr.map {
                    RoomMessage(id: intVal($0["id"]), author: ($0["author"] as? String) ?? "?",
                                kind: ($0["kind"] as? String) ?? "chat", body: ($0["body"] as? String) ?? "",
                                taskId: optIntVal($0["task_id"]), createdAt: doubleVal($0["created_at"]))
                }
            }
            if let arr = taskRes["tasks"] as? [[String: Any]] {
                tasks = arr.map {
                    PackTask(id: intVal($0["id"]), title: ($0["title"] as? String) ?? "",
                             body: ($0["body"] as? String) ?? "", status: ($0["status"] as? String) ?? "",
                             assignee: optStringVal($0["assignee"]), attempts: intVal($0["attempts"]),
                             result: optStringVal($0["result"]), leaseExpires: optDoubleVal($0["lease_expires"]),
                             updatedAt: doubleVal($0["updated_at"]))
                }
            }
            if let arr = memRes["members"] as? [[String: Any]] {
                members = arr.map {
                    MemberInfo(name: ($0["name"] as? String) ?? "?", role: ($0["role"] as? String) ?? "",
                               profile: optStringVal($0["profile"]),
                               lastHeartbeat: optDoubleVal($0["last_heartbeat"]))
                }
            }
            connected = true
            connectionNote = "已连接"
        } catch {
            connected = false
            connectionNote = "连接中断，重试中"
        }
    }

    // MARK: - derived

    var selectedTask: PackTask? { tasks.first { $0.id == selectedTaskId } }

    /// approval_request messages whose task is still waiting and has not
    /// been approved or rejected by the dispatcher afterwards.
    var pendingApprovals: [RoomMessage] {
        messages.filter { m in
            guard m.kind == "approval_request", let tid = m.taskId else { return false }
            guard let t = tasks.first(where: { $0.id == tid }),
                  t.status == "claimed" || t.status == "working" else { return false }
            let decided = messages.contains { x in
                x.id > m.id && x.author == "dispatcher" &&
                (x.body.hasPrefix("批准 任务#\(tid)") || x.body.hasPrefix("拒绝 任务#\(tid)"))
            }
            return !decided
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
                lastActionNote = "操作失败：" + String(describing: error)
            }
        }
    }

    func sendDraft() {
        let body = draftMessage.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !body.isEmpty else { return }
        draftMessage = ""
        act("已发送") { try await $0.call(name: "room_post", args: ["room": "pack-main", "body": body, "kind": "chat"]) }
    }

    func createTask() {
        let tag = "\u{3010}\u{9700}\u{6279}\u{51C6}\u{3011}"
        var title = newTitle.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !title.isEmpty else { return }
        if newNeedsApproval && !title.contains(tag) { title = tag + title }
        let body = newBody
        let assignee = newAssignee
        showCreateTask = false
        newTitle = ""; newBody = ""; newNeedsApproval = false
        act("已派活") { try await $0.call(name: "task_create",
                                          args: ["room": "pack-main", "title": title, "body": body, "assignee_name": assignee]) }
    }

    func approveTask(_ tid: Int) {
        act("已批准任务 #\(tid)") { c in
            _ = try await c.call(name: "room_post",
                                 args: ["room": "pack-main", "body": "批准 任务#\(tid)（群主已在 App 批准）", "kind": "chat"])
            return try await c.call(name: "room_read_new", args: ["room": "pack-main", "since_id": 0, "limit": 1])
        }
    }

    func rejectTask(_ tid: Int) {
        let reason = (rejectReasons[tid] ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let suffix = reason.isEmpty ? "" : "：\(reason)"
        act("已拒绝并取消任务 #\(tid)") { c in
            _ = try await c.call(name: "room_post",
                                  args: ["room": "pack-main", "body": "拒绝 任务#\(tid)\(suffix)", "kind": "chat"])
            return try await c.call(name: "task_cancel", args: ["task_id": tid])
        }
    }

    func reviewTask(_ tid: Int, approve: Bool) {
        act(approve ? "已关单 #\(tid)" : "已打回 #\(tid)") {
            try await $0.call(name: "task_review", args: ["task_id": tid, "approve": approve, "note": "App 操作"])
        }
    }

    func cancelTask(_ tid: Int) {
        act("已取消任务 #\(tid)") { try await $0.call(name: "task_cancel", args: ["task_id": tid]) }
    }
}

struct ContentView: View {
    @StateObject private var state = AppState()

    var body: some View {
        NavigationSplitView {
            List(selection: $state.selection) {
                Label("群聊", systemImage: "text.bubble").tag("room")
                Label("任务板", systemImage: "checklist").tag("tasks")
                Label("批准中心", systemImage: "checkmark.shield").tag("approvals")
                    .badge(state.pendingApprovals.count)
                Label("成员", systemImage: "person.2").tag("members")
                Section {
                    HStack(spacing: 6) {
                        Circle().fill(state.connected ? Color.green : Color.red).frame(width: 8, height: 8)
                        Text(state.connectionNote).font(.caption).foregroundStyle(.secondary)
                    }
                    Button {
                        state.showCreateTask = true
                    } label: {
                        Label("派活", systemImage: "plus.circle.fill")
                    }
                    .buttonStyle(.borderedProminent)
                    if !state.lastActionNote.isEmpty {
                        Text(state.lastActionNote).font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            .navigationTitle("Pack")
        } content: {
            switch state.selection {
            case "tasks": TaskListView(state: state)
            case "approvals": ApprovalsView(state: state)
            case "members": MemberListView(members: state.members)
            default: RoomView(state: state)
            }
        } detail: {
            if state.selection == "tasks", let t = state.selectedTask {
                TaskDetailView(state: state, task: t)
            } else {
                Text("Pack 操作台").foregroundStyle(.secondary)
            }
        }
        .sheet(isPresented: $state.showCreateTask) { CreateTaskView(state: state) }
        .onAppear { state.start() }
        .frame(minWidth: 900, minHeight: 520)
    }
}

struct RoomView: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(spacing: 0) {
            List(state.messages) { m in
                Button {
                    if let tid = m.taskId {
                        state.selectedTaskId = tid
                        state.selection = "tasks"
                    }
                } label: {
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 6) {
                            Text(m.author).font(.caption).bold()
                            if m.kind != "chat" {
                                Text(m.kind).font(.caption2).padding(.horizontal, 5).padding(.vertical, 1)
                                    .background(Color.secondary.opacity(0.2)).clipShape(Capsule())
                            }
                            if let t = m.taskId { Text("任务 #\(t)").font(.caption2).foregroundStyle(.secondary) }
                        }
                        Text(m.body).textSelection(.enabled).foregroundStyle(.primary)
                    }
                    .padding(.vertical, 2)
                }
                .buttonStyle(.plain)
            }
            Divider()
            HStack(spacing: 8) {
                TextField("说点什么…", text: $state.draftMessage)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { state.sendDraft() }
                Button("发送") { state.sendDraft() }
                    .buttonStyle(.borderedProminent)
            }
            .padding(8)
        }
        .navigationTitle("群聊 pack-main")
    }
}

struct TaskListView: View {
    @ObservedObject var state: AppState
    private let columns = ["open", "claimed", "working", "review", "blocked", "done", "cancelled"]

    var body: some View {
        List(selection: $state.selectedTaskId) {
            ForEach(columns, id: \.self) { status in
                let group = state.tasks.filter { $0.status == status }
                if !group.isEmpty {
                    Section(header: Text("\(group.first!.statusLabel) (\(group.count))")) {
                        ForEach(group) { t in
                            VStack(alignment: .leading, spacing: 2) {
                                Text("#\(t.id) \(t.title)").bold()
                                HStack(spacing: 8) {
                                    if let a = t.assignee { Text("执行：\(a)").font(.caption).foregroundStyle(.secondary) }
                                    Text("尝试 \(t.attempts) 次").font(.caption).foregroundStyle(.secondary)
                                }
                                if let r = t.result { Text(r).font(.callout).lineLimit(2) }
                            }
                            .padding(.vertical, 2)
                            .tag(Optional(t.id))
                        }
                    }
                }
            }
        }
        .navigationTitle("任务板")
    }
}

struct TaskDetailView: View {
    @ObservedObject var state: AppState
    let task: PackTask

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                Text("#\(task.id) \(task.title)").font(.title3).bold()
                HStack(spacing: 8) {
                    Text(task.statusLabel).font(.caption).padding(.horizontal, 7).padding(.vertical, 2)
                        .background(Color.secondary.opacity(0.2)).clipShape(Capsule())
                    if let a = task.assignee { Text("执行：\(a)").font(.caption).foregroundStyle(.secondary) }
                    Text("尝试 \(task.attempts) 次").font(.caption).foregroundStyle(.secondary)
                    if let lease = task.leaseExpires {
                        let left = Int(lease - Date().timeIntervalSince1970)
                        if left > 0 { Text("租约剩 \(left / 60) 分钟").font(.caption).foregroundStyle(.secondary) }
                    }
                }
                if !task.body.isEmpty {
                    Text(task.body).textSelection(.enabled)
                }
                if let r = task.result {
                    Divider()
                    Text("成果").font(.headline)
                    Text(r).textSelection(.enabled)
                }
                Divider()
                HStack(spacing: 10) {
                    if task.status == "review" {
                        Button("验收关单") { state.reviewTask(task.id, approve: true) }
                            .buttonStyle(.borderedProminent)
                        Button("打回重做") { state.reviewTask(task.id, approve: false) }
                    }
                    if task.status == "open" || task.status == "claimed" || task.status == "working" {
                        Button("取消任务", role: .destructive) { state.cancelTask(task.id) }
                    }
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .navigationTitle("任务详情")
    }
}

struct ApprovalsView: View {
    @ObservedObject var state: AppState

    var body: some View {
        List {
            if state.pendingApprovals.isEmpty {
                Text("没有待批准的任务").foregroundStyle(.secondary)
            }
            ForEach(state.pendingApprovals) { m in
                VStack(alignment: .leading, spacing: 6) {
                    if let tid = m.taskId, let t = state.tasks.first(where: { $0.id == tid }) {
                        Text("#\(tid) \(t.title)").bold()
                        if !t.body.isEmpty { Text(t.body).font(.callout).foregroundStyle(.secondary).lineLimit(3) }
                    }
                    Text(m.body).font(.caption).foregroundStyle(.secondary)
                    HStack(spacing: 8) {
                        Button("批准") { if let tid = m.taskId { state.approveTask(tid) } }
                            .buttonStyle(.borderedProminent)
                        TextField("拒绝原因（可选）", text: reasonBinding(for: m.taskId ?? -1))
                            .textFieldStyle(.roundedBorder)
                        Button("拒绝", role: .destructive) { if let tid = m.taskId { state.rejectTask(tid) } }
                    }
                }
                .padding(.vertical, 4)
            }
        }
        .navigationTitle("批准中心")
    }

    private func reasonBinding(for tid: Int) -> Binding<String> {
        Binding<String>(
            get: { state.rejectReasons[tid] ?? "" },
            set: { state.rejectReasons[tid] = $0 }
        )
    }
}

struct CreateTaskView: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("派活").font(.title3).bold()
            TextField("标题", text: $state.newTitle)
                .textFieldStyle(.roundedBorder)
            TextField("正文（把要求写清楚）", text: $state.newBody, axis: .vertical)
                .textFieldStyle(.roundedBorder)
            Picker("派给", selection: $state.newAssignee) {
                ForEach(state.members.filter { $0.role == "member" }) { m in
                    Text(m.name).tag(m.name)
                }
            }
            Toggle("需要我批准才能执行", isOn: $state.newNeedsApproval)
            HStack {
                Spacer()
                Button("取消") { state.showCreateTask = false }
                Button("建卡派活") { state.createTask() }
                    .buttonStyle(.borderedProminent)
                    .disabled(state.newTitle.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(20)
        .frame(width: 460)
    }
}

struct MemberListView: View {
    let members: [MemberInfo]

    var body: some View {
        List(members) { m in
            HStack(spacing: 10) {
                Circle().fill(m.isOnline ? Color.green : Color.gray).frame(width: 9, height: 9)
                VStack(alignment: .leading, spacing: 1) {
                    Text(m.name).bold()
                    Text([m.role, m.profile].compactMap { $0 }.joined(separator: " · "))
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Text(m.isOnline ? "在线" : "离线").font(.caption).foregroundStyle(.secondary)
            }
            .padding(.vertical, 2)
        }
        .navigationTitle("成员")
    }
}

@main
struct PackApp: App {
    var body: some Scene {
        WindowGroup {
            ContentView()
        }
    }
}
