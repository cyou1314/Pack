import SwiftUI
import Security
import PackCore

@MainActor
extension AppState {
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
}
