import SwiftUI
import PackCore

@MainActor
extension AppState {
    // MARK: - Room navigation and lifecycle

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
}
