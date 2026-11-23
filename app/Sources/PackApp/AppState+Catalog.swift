import Foundation
import SwiftUI
import PackCore

@MainActor
extension AppState {
    // MARK: - Skill and task-template catalog

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

}
