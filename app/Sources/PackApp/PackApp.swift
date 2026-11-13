import SwiftUI

@MainActor
final class AppState: ObservableObject {
    @Published var messages: [RoomMessage] = []
    @Published var tasks: [PackTask] = []
    @Published var members: [MemberInfo] = []
    @Published var connectionNote: String = "未连接"
    @Published var connected: Bool = false
    @Published var selection: String? = "room"

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
                try? await Task.sleep(nanoseconds: 20_000_000_000)
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
                             result: optStringVal($0["result"]), updatedAt: doubleVal($0["updated_at"]))
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
}

struct ContentView: View {
    @StateObject private var state = AppState()

    var body: some View {
        NavigationSplitView {
            List(selection: $state.selection) {
                Label("群聊", systemImage: "text.bubble").tag("room")
                Label("任务板", systemImage: "checklist").tag("tasks")
                Label("成员", systemImage: "person.2").tag("members")
            }
            .navigationTitle("Pack")
            HStack(spacing: 6) {
                Circle().fill(state.connected ? Color.green : Color.red).frame(width: 8, height: 8)
                Text(state.connectionNote).font(.caption).foregroundStyle(.secondary)
            }
            .padding(8)
        } detail: {
            switch state.selection {
            case "tasks": TaskBoardView(tasks: state.tasks)
            case "members": MemberListView(members: state.members)
            default: RoomView(messages: state.messages)
            }
        }
        .onAppear { state.start() }
        .frame(minWidth: 760, minHeight: 480)
    }
}

struct RoomView: View {
    let messages: [RoomMessage]

    var body: some View {
        List(messages) { m in
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(m.author).font(.caption).bold()
                    if m.kind != "chat" {
                        Text(m.kind).font(.caption2).padding(.horizontal, 5).padding(.vertical, 1)
                            .background(Color.secondary.opacity(0.2)).clipShape(Capsule())
                    }
                    if let t = m.taskId { Text("任务 #\(t)").font(.caption2).foregroundStyle(.secondary) }
                }
                Text(m.body).textSelection(.enabled)
            }
            .padding(.vertical, 2)
        }
        .navigationTitle("群聊 pack-main")
    }
}

struct TaskBoardView: View {
    let tasks: [PackTask]
    private let columns = ["open", "claimed", "working", "review", "blocked", "done"]

    var body: some View {
        List {
            ForEach(columns, id: \.self) { status in
                let group = tasks.filter { $0.status == status }
                if !group.isEmpty {
                    Section(header: Text("\(group.first!.statusLabel) (\(group.count))")) {
                        ForEach(group) { t in
                            VStack(alignment: .leading, spacing: 2) {
                                Text("#\(t.id) \(t.title)").bold()
                                HStack(spacing: 8) {
                                    if let a = t.assignee { Text("执行：\(a)").font(.caption).foregroundStyle(.secondary) }
                                    Text("尝试 \(t.attempts) 次").font(.caption).foregroundStyle(.secondary)
                                }
                                if let r = t.result {
                                    Text(r).font(.callout).foregroundStyle(.primary)
                                }
                            }
                            .padding(.vertical, 2)
                        }
                    }
                }
            }
        }
        .navigationTitle("任务板")
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
