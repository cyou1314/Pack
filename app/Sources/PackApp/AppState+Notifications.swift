import Foundation
import SwiftUI
import AppKit

@MainActor
extension AppState {
    // MARK: - Notifications and unread state

    func processNotificationSnapshot(room: String) {
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

    func navigateNotification(kind: String, id: Int?, member: String?) {
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

    func refreshUnreadCounts(_ snapshot: [RoomItem]) async {
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

    func scheduleMarkRoomRead(_ room: String, generation: Int) {
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
}
