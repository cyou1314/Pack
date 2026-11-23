import SwiftUI
import AppKit
import UniformTypeIdentifiers
import WebKit

struct SettingsView: View {
    @ObservedObject var state: AppState
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: DesignTokens.settingsGap) {
                Text("设置").font(DesignTokens.fontTitle)
                GroupBox("中枢地址") {
                    VStack(alignment: .leading, spacing: DesignTokens.settingsGap) {
                        HStack { TextField("中枢地址", text: $state.settingsHubURL).textFieldStyle(.roundedBorder); Button("保存") { state.saveHubURL() } }
                        Text("地址会自动跟随中枢发布地址，手动保存后本次运行以手动为准")
                            .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
                        Text("地址源令牌").font(DesignTokens.fontUI)
                        HStack {
                            SecureField("地址源令牌（可留空）", text: $state.settingsAddrToken).textFieldStyle(.roundedBorder)
                            Button("保存令牌") { state.saveAddrToken() }
                        }
                        Text("用于从私有仓获取中枢地址，可留空；保存空值会清除已存令牌。")
                            .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
                        Text(state.addrTokenStatus).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
                        Text(state.credentialStatus)
                            .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
                        if !state.settingsMessage.isEmpty { Text(state.settingsMessage).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText) }
                    }.frame(maxWidth: .infinity, alignment: .leading)
                }
                GroupBox("批准默认模式") {
                    VStack(alignment: .leading, spacing: DesignTokens.detailSpacing) {
                        Toggle("新任务默认需要批准", isOn: $state.approvalDefault).onChange(of: state.approvalDefault) { value in state.newNeedsApproval = value }
                        Text(state.notificationStatus).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
                        Text("通知与 Dock 数字基于当前房间已加载的待批准和待验收项目；离线通知基于已加载成员。")
                            .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
                    }.frame(maxWidth: .infinity, alignment: .leading)
                }
                GroupBox("成员巡逻间隔") {
                    VStack(alignment: .leading, spacing: DesignTokens.detailSpacing) {
                        Text("中枢尚未提供成员巡逻间隔设置接口。").font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
                        Picker("本 App 状态刷新间隔", selection: $state.appRefreshSeconds) {
                            Text("15 秒").tag(15); Text("30 秒").tag(30); Text("60 秒").tag(60)
                        }.frame(maxWidth: DesignTokens.settingsFieldWidth)
                    }.frame(maxWidth: .infinity, alignment: .leading)
                }
                GroupBox("数据与备份位置") {
                    VStack(alignment: .leading, spacing: DesignTokens.detailSpacing) {
                        Text("App 配置：~/.pack/config.json").textSelection(.enabled)
                        Text("草稿和界面偏好：本机应用偏好设置")
                        Text("中枢备份位置由中枢管理，App 暂无法读取或修改。")
                            .foregroundStyle(DesignTokens.subText)
                    }.font(DesignTokens.fontSmall).frame(maxWidth: .infinity, alignment: .leading)
                }
            }.padding(DesignTokens.settingsInset).frame(maxWidth: DesignTokens.settingsWidth, alignment: .leading).frame(maxWidth: .infinity)
        }.task { await PackNotifications.shared.refreshStatus() }
    }
}

struct OnboardingView: View {
    @ObservedObject var state: AppState
    var body: some View {
        VStack(alignment: .leading, spacing: DesignTokens.settingsGap) {
            Text("欢迎使用 Pack").font(DesignTokens.fontTitle)
            Text("1. 连接中枢：请先配置中枢地址。")
            Text("2. 确认身份：请安全配置本机凭证，App 会尝试迁入钥匙串。")
            Text("3. 配置完成后重新打开 App，进入主房间。")
            Button("知道了") { state.showOnboarding = false }
        }.padding(DesignTokens.settingsInset).frame(width: DesignTokens.memberLoginWidth)
    }
}

struct MemberDirectory: View {
    @ObservedObject var state: AppState
    private func rowStatus(_ member: MemberInfo) -> String {
        if state.loginStatus(member.loginState) != nil { return state.memberStatus(member) }
        if state.pendingMembers.contains(member.name) && member.lastHeartbeat == nil { return state.memberStatus(member) }
        guard member.isOnline else {
            guard let heartbeat = member.lastHeartbeat else { return "离线 · 暂无记录" }
            let minutes = Int(max(0, Date().timeIntervalSince1970 - heartbeat) / 60)
            return "离线 · \(minutes) 分钟前还在"
        }
        return state.memberStatus(member)
    }
    var body: some View {
        HStack(alignment: .top, spacing: DesignTokens.memberGap) {
            ScrollView {
                LazyVStack(spacing: DesignTokens.memberListRowSpacing) {
                    if !state.memberRemovalReport.isEmpty {
                        VStack(alignment: .leading, spacing: DesignTokens.memberGap) {
                            Text("最近一次成员移除结果").font(DesignTokens.fontTitle)
                            Text(state.memberRemovalReport).font(DesignTokens.fontSmall).textSelection(.enabled)
                            Button("关闭结果") {
                                state.memberRemovalReport = ""
                                UserDefaults.standard.removeObject(forKey: "pack.memberRemovalReport")
                            }.buttonStyle(.plain)
                        }.frame(maxWidth: .infinity, alignment: .leading).padding(DesignTokens.memberInset)
                            .background(DesignTokens.bubbleAI).clipShape(RoundedRectangle(cornerRadius: DesignTokens.radiusCard))
                    }
                    ForEach(state.members.sorted { a, b in
                        let aa = a.isOnline && !a.quotaExhausted; let bb = b.isOnline && !b.quotaExhausted
                        return aa == bb ? a.name < b.name : aa && !bb
                    }) { member in
                        Button { state.toggleMember(member.name) } label: {
                            HStack(spacing: DesignTokens.memberGap) {
                                MemberAvatarView(name: member.name, member: member, size: DesignTokens.memberListAvatar).id(state.avatarRevision)
                                VStack(alignment: .leading, spacing: DesignTokens.memberListTextSpacing) {
                                    Text(member.name).font(DesignTokens.fontTitle)
                                    Text(rowStatus(member)).font(DesignTokens.fontSmall)
                                        .foregroundStyle(member.quotaExhausted ? DesignTokens.danger : DesignTokens.subText)
                                }
                                Spacer()
                            }.padding(.horizontal, DesignTokens.shellInset)
                                .frame(maxWidth: .infinity, minHeight: DesignTokens.memberListRowHeight, alignment: .leading)
                                .contentShape(Rectangle())
                        }.buttonStyle(.plain)
                    }
                    Button("＋ 添加成员") { state.memberActionError = ""; state.showAddMember = true }.buttonStyle(.plain).foregroundStyle(DesignTokens.blue)
                }.padding(DesignTokens.memberInset)
            }
            if let name = state.selectedMemberName, let member = state.members.first(where: { $0.name == name }) {
                MemberDetail(state: state, member: member).frame(width: DesignTokens.memberPanelWidth)
            }
        }.frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}

struct MemberDetail: View {
    @ObservedObject var state: AppState
    let member: MemberInfo
    private var recentTasks: [PackTask] {
        Array(state.tasks.filter { $0.assignee == member.name && ["done", "review"].contains($0.status) }
            .sorted { $0.updatedAt > $1.updatedAt }.prefix(DesignTokens.memberRecentLimit))
    }
    private func duration(_ task: PackTask) -> String {
        if state.memberRunsFailed.contains(task.id) { return "用时读取失败" }
        guard let seconds = state.memberRunDurations[task.id] else { return state.memberRunsLoading ? "用时读取中" : "用时暂无记录" }
        let value = Int(seconds)
        return "最近执行 \(value / 60) 分 \(value % 60) 秒"
    }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: DesignTokens.memberGap) {
                HStack {
                    MemberAvatarView(name: member.name, member: member, size: DesignTokens.memberAvatarLarge).id(state.avatarRevision)
                    VStack(alignment: .leading) { Text(member.name).font(DesignTokens.fontTitle); Text(state.memberStatus(member)).font(DesignTokens.fontSmall) }
                    Spacer()
                    Button { state.selectedMemberName = nil } label: { Image(systemName: "xmark") }.buttonStyle(.plain).help("关闭成员详情")
                }
                Text("最近心跳：\(member.lastHeartbeat.map { Date(timeIntervalSince1970: $0).formatted() } ?? "暂无记录") · 本周已交活：\(member.weekDone.map(String.init) ?? "暂无完整统计")")
                    .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
                Text("脑源：\(member.provider == nil || member.provider == "muse" ? "Muse" : member.provider!) · \(state.loginStatus(member.loginState) ?? "暂无登录状态")")
                    .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
                if member.loginState == "logged_in" && (member.provider == nil || member.provider == "muse") {
                    Button(state.memberHandoffInProgress == member.name ? "正在交接…" : "将会话交给本机引擎") { state.handoffMember(member) }
                        .disabled(state.memberHandoffInProgress != nil)
                    if let result = state.memberHandoffResults[member.name] { Text(result).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText).textSelection(.enabled) }
                }
                if member.provider == "muse", state.memberHandoffResults[member.name] != nil {
                    Button("检查引擎会话状态") { state.checkMemberEngine(member) }
                        .disabled(state.memberHandoffInProgress != nil)
                    if member.loginState != "logged_in", let result = state.memberHandoffResults[member.name] { Text(result).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText).textSelection(.enabled) }
                }
                Divider()
                HStack { QuotaRingView(usedPct: member.quotaWeekUsedPct, stale: member.quotaIsStale); VStack(alignment: .leading) {
                    Text("周额度").font(DesignTokens.fontTitle)
                    Text(member.quotaWeekUsedPct.map { "已用 \(Int($0.rounded()))%" } ?? "暂无数据").font(DesignTokens.fontSmall)
                    Text("附加 token：\(member.quotaExtraLeft.map { QuotaDisplay.tokens($0) } ?? "暂无数据")").font(DesignTokens.fontSmall)
                }.foregroundStyle(member.quotaIsStale ? DesignTokens.quotaStaleText : DesignTokens.ink) }
                HStack(spacing: DesignTokens.shellGap) {
                    Text("额度更新时间：\(member.quotaUpdatedAt.map { QuotaDisplay.updatedDate($0) } ?? "暂无")")
                    if let freshness = QuotaDisplay.freshness(member) { Text(freshness) }
                }.font(DesignTokens.fontSmall)
                    .foregroundStyle(member.quotaIsStale ? DesignTokens.quotaStaleText : DesignTokens.subText)
                Divider()
                Text("当前房间的任务").font(DesignTokens.fontTitle)
                let active = state.tasks.filter { $0.assignee == member.name && ["claimed", "working"].contains($0.status) }
                if active.isEmpty { Text("暂无在跑任务").font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText) }
                ForEach(active) { task in Text("#\(task.id) \(task.title)").font(DesignTokens.fontSmall) }
                Text("最近交活（当前房间）").font(DesignTokens.fontTitle)
                if recentTasks.isEmpty { Text("暂无交活").font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText) }
                ForEach(recentTasks) { task in
                    Text("#\(task.id) \(task.title) · \(task.envelope?.verdict ?? task.statusLabel) · \(duration(task)) · 产物 \(task.envelope?.artifacts.count ?? 0)").font(DesignTokens.fontSmall)
                }
                Text("已装技能").font(DesignTokens.fontTitle)
                if !state.memberSkillsStatus.isEmpty { Text(state.memberSkillsStatus).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText) }
                ForEach(state.memberSkills) { skill in
                    Text("\(skill.name) · v\(skill.version)").font(DesignTokens.fontSmall)
                }
                if member.name != "owner" {
                    if member.role == "member" && member.kind != "external" { Button("成员本人登录 / 重新登录") { state.openMemberLogin(member) }.buttonStyle(.bordered) }
                    Button("更换头像") { state.openAvatarPicker(member) }.buttonStyle(.bordered)
                    if member.role == "member" { Button("移除成员") { state.showRemoveMember = true }.foregroundStyle(DesignTokens.danger).disabled(state.memberRemovalInProgress) }
                }
            }.padding(DesignTokens.memberInset)
        }.background(DesignTokens.bubbleAI)
            .task(id: member.name) { await state.loadMemberSkills(member.name) }
            .task(id: member.name + ":" + recentTasks.map { "\($0.id)-\($0.updatedAt)" }.joined(separator: ",")) {
                await state.loadMemberRunDurations(member.name, taskIds: recentTasks.map(\.id))
            }
    }
}

struct AvatarPickerView: View {
    @ObservedObject var state: AppState
    var body: some View {
        VStack(alignment: .leading, spacing: DesignTokens.memberGap) {
            Text("更换头像 · \(state.avatarTargetName)").font(DesignTokens.fontTitle)
            ParameterAvatar(species: state.editAvatarSpecies,
                            color: DesignTokens.avatarPalettes[state.editAvatarPalette] ?? DesignTokens.ink,
                            background: DesignTokens.avatarBackgrounds[state.editAvatarBackground] ?? DesignTokens.bubbleAI,
                            accessory: state.editAvatarAccessory)
                .frame(width: DesignTokens.memberAvatarLarge, height: DesignTokens.memberAvatarLarge)
            Picker("动物", selection: $state.editAvatarSpecies) { ForEach(["fox", "owl", "wolf", "cat", "panda", "marten", "bear", "rabbit", "deer", "raccoon", "tiger", "penguin"], id: \.self) { Text($0).tag($0) } }
            Picker("配色", selection: $state.editAvatarPalette) { ForEach(["orange", "violet", "ocean", "forest", "rose", "amber", "teal", "indigo", "crimson", "slate"], id: \.self) { Text($0).tag($0) } }
            Picker("配饰", selection: $state.editAvatarAccessory) { ForEach(["none", "star-clip", "glasses", "headset", "scarf", "cap"], id: \.self) { Text($0).tag($0) } }
            Picker("底色", selection: $state.editAvatarBackground) { ForEach(["cream", "mint", "sky", "peach", "lavender", "sand"], id: \.self) { Text($0).tag($0) } }
            Text("组合占用与有效性由中枢检查；占用时请选择其他组合。")
                .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
            if !state.avatarSaveError.isEmpty { Text(state.avatarSaveError).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.danger).textSelection(.enabled) }
            HStack {
                if ["dispatcher", "absent"].contains(state.avatarTargetName) {
                    Button("恢复默认") { state.restoreDesignedAvatar() }.disabled(state.avatarSaving)
                }
                Spacer()
                Button("取消") { state.showAvatarPicker = false }.disabled(state.avatarSaving)
                Button(state.avatarSaving ? "保存中…" : "保存") { state.saveMemberAvatar() }.buttonStyle(.borderedProminent).disabled(state.avatarSaving)
            }
        }.padding(DesignTokens.chatInset).frame(width: DesignTokens.memberLoginWidth)
    }
}

struct AddMemberView: View {
    @ObservedObject var state: AppState
    var body: some View {
        VStack(alignment: .leading, spacing: DesignTokens.memberGap) {
            Text("添加成员").font(DesignTokens.fontTitle)
            TextField("成员名字", text: $state.memberNameInput).textFieldStyle(.roundedBorder)
            Text("脑源：Muse").font(DesignTokens.fontUI)
            let avatar = AvatarInfo(glyph: "?", c1: "#1B7CF5", c2: "#1B7CF5", species: state.avatarSpecies, palette: state.avatarPalette, accessory: state.avatarAccessory, bg: state.avatarBackground, combo: nil)
            let preview = MemberInfo(name: state.memberNameInput, role: "member", profile: nil, avatar: avatar, lastHeartbeat: nil)
            HStack(spacing: DesignTokens.memberGap) {
                MemberAvatarView(name: preview.name, member: preview, size: DesignTokens.memberAvatarLarge, showStatus: false)
                Text("选择头像组合；如已被占用，中枢会拒绝并提示换一个。")
                    .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
            }
            Picker("动物", selection: $state.avatarSpecies) { ForEach(["fox", "owl", "wolf", "cat", "panda", "marten", "bear", "rabbit", "deer", "raccoon", "tiger", "penguin"], id: \.self) { Text($0).tag($0) } }
            Picker("配色", selection: $state.avatarPalette) { ForEach(["orange", "violet", "ocean", "forest", "rose", "amber", "teal", "indigo", "crimson", "slate"], id: \.self) { Text($0).tag($0) } }
            Picker("配饰", selection: $state.avatarAccessory) { ForEach(["none", "star-clip", "glasses", "headset", "scarf", "cap"], id: \.self) { Text($0).tag($0) } }
            Picker("底色", selection: $state.avatarBackground) { ForEach(["cream", "mint", "sky", "peach", "lavender", "sand"], id: \.self) { Text($0).tag($0) } }
            Text("成员在 Pack 内本人登录账号；账号密码和验证码由本人输入，App 不接收或保存。")
                .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
            if !state.memberActionError.isEmpty { Text(state.memberActionError).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.danger) }
            HStack { Spacer(); Button("取消") { state.showAddMember = false }; Button("发起添加") { state.registerMember() }.buttonStyle(.borderedProminent).tint(DesignTokens.blue) }
        }.padding(DesignTokens.chatInset).frame(width: DesignTokens.memberLoginWidth)
    }
}

struct MemberLoginShell: View {
    @ObservedObject var state: AppState
    private var loginURL: URL? { MemberLoginWebView.startURL(provider: state.loginTargetProvider) }
    var body: some View {
        VStack(spacing: DesignTokens.memberGap) {
            Text("成员本人登录 · \(state.loginTargetName)").font(DesignTokens.fontTitle)
            Text("账号、密码和验证码由成员本人在服务方页面输入。")
                .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
            if let url = loginURL {
                MemberLoginWebView(memberName: state.loginTargetName, url: url, onAuthenticated: { state.completeMemberLogin() }, onError: { state.memberActionError = $0 })
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .clipShape(RoundedRectangle(cornerRadius: DesignTokens.radiusCard))
            } else {
                Text("该脑源暂不支持内嵌登录").font(DesignTokens.fontUI).foregroundStyle(DesignTokens.subText)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
            if !state.memberActionError.isEmpty { Text(state.memberActionError).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.danger).textSelection(.enabled) }
            HStack {
                Button("关闭") { state.showMemberLogin = false }.disabled(state.memberLoginSaving)
                Spacer()
                Button(state.memberLoginSaving ? "保存中…" : "我已登录") { state.completeMemberLogin() }
                    .buttonStyle(.borderedProminent).tint(DesignTokens.blue).disabled(state.memberLoginSaving || loginURL == nil)
            }
        }.padding(DesignTokens.chatInset).frame(width: DesignTokens.memberLoginSheetWidth, height: DesignTokens.memberLoginSheetHeight)
    }
}

