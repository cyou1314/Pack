import SwiftUI
import AppKit
import UniformTypeIdentifiers
import WebKit

struct ContentView: View {
    @StateObject private var state = AppState()
    var body: some View {
        HStack(spacing: 0) {
            Sidebar(state: state)
                .frame(width: state.showSidebar ? DesignTokens.sidebarWidth : DesignTokens.railWidth)
                .clipped()
            Rectangle().fill(DesignTokens.hairline).frame(width: DesignTokens.shellDivider)
            VStack(spacing: 0) {
                CenterHeader(state: state)
                Rectangle().fill(PackStyle.line).frame(height: 1)
                if state.connectionFailed {
                    Text("中枢连接已断开 · 正在重连")
                        .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.amber)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, DesignTokens.shellHeaderInset)
                        .padding(.vertical, DesignTokens.connectionBannerVertical)
                        .background(DesignTokens.connectionBannerBackground)
                        .transition(.opacity)
                }
                Group {
                    if state.selection == "tasks", let task = state.selectedTask {
                        ScrollView {
                            VStack(alignment: .leading, spacing: 16) {
                                Button { state.selectedTaskId = nil } label: { Label("返回任务板", systemImage: "arrow.left").font(.system(size: 12, weight: .medium)) }.buttonStyle(.plain).foregroundStyle(PackStyle.accent)
                                TaskDetail(state: state, task: task)
                            }.frame(maxWidth: 740).frame(maxWidth: .infinity).padding(24)
                        }
                    } else {
                        switch state.selection {
                        case "tasks": TaskBoard(state: state)
                        case "approvals": ApprovalsView(state: state)
                        case "settings": SettingsView(state: state)
                        case "members": MemberDirectory(state: state)
                        default: RoomView(state: state)
                            .id(state.currentRoom)
                            .transition(.asymmetric(insertion: .move(edge: .trailing).combined(with: .opacity), removal: .move(edge: .leading).combined(with: .opacity)))
                        }
                    }
                }.frame(maxWidth: .infinity, maxHeight: .infinity)
                    .animation(DesignTokens.animStd, value: state.currentRoom)
                if state.selection == "room" {
                    Composer(state: state)
                        .disabled(!state.connected)
                        .opacity(state.connected ? 1 : DesignTokens.connectionDisabledOpacity)
                }
            }.frame(maxWidth: .infinity, maxHeight: .infinity).background(PackStyle.canvas)
        }
        .background(PackStyle.canvas)
        .background {
            Button("搜索") { state.showSearch = true }.keyboardShortcut("k", modifiers: .command)
                .frame(width: DesignTokens.searchShortcutHiddenSize, height: DesignTokens.searchShortcutHiddenSize).opacity(DesignTokens.searchShortcutHiddenOpacity)
        }
        .sheet(isPresented: $state.showSearch) { SearchPanel(state: state) }
        .sheet(isPresented: $state.showCreateTask) { CreateTaskView(state: state) }
        .sheet(isPresented: $state.showRoomDialog) { RoomNameDialog(state: state) }
        .sheet(isPresented: $state.showAddMember) { AddMemberView(state: state) }
        .sheet(isPresented: $state.showAvatarPicker) { AvatarPickerView(state: state) }
        .sheet(isPresented: $state.showMemberLogin) { MemberLoginShell(state: state) }
        .sheet(isPresented: $state.showCatalog) { CatalogView(state: state) }
        .sheet(isPresented: $state.showArtifactPreview) { ArtifactPreview(state: state) }
        .sheet(isPresented: $state.showOnboarding) { OnboardingView(state: state) }
        .alert("移除成员", isPresented: $state.showRemoveMember) {
            Button("取消", role: .cancel) { }
            Button("确认移除", role: .destructive) { state.removeSelectedMember() }
        } message: {
            Text("移除后将在本机清除该成员登录分区及引擎账号与分区，并撤销中枢访问；各项结果会逐项显示。")
        }
        .onAppear { state.start() }
        .preferredColorScheme(.light)
        .animation(DesignTokens.animFast, value: state.connectionFailed)
        .frame(minWidth: DesignTokens.shellMinWidth, minHeight: DesignTokens.shellMinHeight)
    }
}

struct Sidebar: View {
    @ObservedObject var state: AppState
    @Namespace private var quotaMotion
    private var member: MemberInfo? { state.members.first { $0.name == "absent" } }
    private func toggle() { withAnimation(DesignTokens.animStd) { state.showSidebar.toggle() } }
    private func select(_ key: String) {
        state.selection = key
        if key == "tasks" { state.selectedTaskId = nil }
    }
    private var logo: some View {
        Group {
            if let url = Bundle.main.url(forResource: "AppIcon", withExtension: "icns"), let icon = NSImage(contentsOf: url) {
                Image(nsImage: icon).resizable().interpolation(.high)
            } else { Image(systemName: "square.stack.3d.up.fill") }
        }.frame(width: DesignTokens.shellLogo, height: DesignTokens.shellLogo)
    }
    private func row(_ key: String, _ title: String, _ icon: String) -> some View {
        Button { select(key) } label: {
            HStack(spacing: DesignTokens.shellGap) {
                Image(systemName: icon).frame(width: DesignTokens.shellIcon)
                if state.showSidebar { Text(title).transition(.opacity); Spacer(minLength: 0) }
                if key == "room" && !state.showSidebar && state.hasUnreadRooms {
                    Circle().fill(DesignTokens.blue).frame(width: DesignTokens.radiusSmall, height: DesignTokens.radiusSmall)
                }
                if key == "approvals" && !state.pendingApprovals.isEmpty {
                    Circle().fill(DesignTokens.amber).frame(width: DesignTokens.radiusSmall, height: DesignTokens.radiusSmall)
                }
            }.font(DesignTokens.fontUI)
                .foregroundStyle(state.selection == key ? DesignTokens.blue : DesignTokens.ink)
                .frame(maxWidth: .infinity, minHeight: DesignTokens.shellRow)
                .background(state.selection == key ? DesignTokens.blue.opacity(DesignTokens.shellSelectionOpacity) : Color.clear, in: RoundedRectangle(cornerRadius: DesignTokens.radiusCard))
                .contentShape(Rectangle())
        }.buttonStyle(.plain).help(title)
    }
    var body: some View {
        VStack(spacing: DesignTokens.shellGap) {
            HStack(spacing: DesignTokens.shellGap) {
                Button(action: toggle) { logo }.buttonStyle(.plain).help(state.showSidebar ? "收起侧栏" : "展开侧栏")
                if state.showSidebar {
                    Text("Pack").font(DesignTokens.shellTitle)
                    Spacer(minLength: 0)
                    Button { state.sidebarPinned.toggle() } label: {
                        Image(systemName: state.sidebarPinned ? "pin.fill" : "pin")
                            .foregroundStyle(state.sidebarPinned ? DesignTokens.blue : DesignTokens.subText)
                    }.buttonStyle(.plain).help(state.sidebarPinned ? "取消固定侧栏" : "固定侧栏")
                    Button(action: toggle) { Image(systemName: "sidebar.left") }.buttonStyle(.plain).help("收起侧栏")
                }
            }.frame(height: DesignTokens.shellRow).padding(.top, DesignTokens.shellTop)
            Button { state.openRoomDialog() } label: {
                HStack(spacing: DesignTokens.shellGap) {
                    Image(systemName: "square.and.pencil")
                    if state.showSidebar { Text("新对话"); Spacer(minLength: 0) }
                }.font(DesignTokens.fontUI).frame(maxWidth: .infinity, minHeight: DesignTokens.shellRow)
                    .background(DesignTokens.shellSurface, in: RoundedRectangle(cornerRadius: DesignTokens.radiusCard))
            }.buttonStyle(.plain).help("新对话")
            if state.showSidebar {
                Button { state.showSearch = true } label: {
                    HStack(spacing: DesignTokens.shellGap) { Image(systemName: "magnifyingglass"); Text("搜索"); Spacer(); Text("⌘K").foregroundStyle(DesignTokens.subText) }
                        .font(DesignTokens.fontUI).frame(maxWidth: .infinity, minHeight: DesignTokens.shellRow).contentShape(Rectangle())
                }.buttonStyle(.plain).help("搜索")
            }
            row("approvals", "收件箱", "tray")
            row("room", "对话", "bubble.left.and.bubble.right")
            row("tasks", "任务板", "square.grid.2x2")
            row("members", "成员", "person.2")
            if state.showSidebar {
                HStack { Text("房间"); Spacer() }.font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText).padding(.top, DesignTokens.shellInset)
                ForEach(state.rooms) { room in
                    HStack(spacing: DesignTokens.shellGap) {
                        Button { state.selectRoom(room.name); if !state.sidebarPinned { toggle() } } label: {
                            HStack {
                                Image(systemName: "bubble.left"); Text(room.name).lineLimit(1); Spacer(minLength: 0)
                                if !(state.roomDrafts[room.name] ?? "").isEmpty { Text("草稿").font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.amber) }
                                if state.hasUnread(room) {
                                    if let count = state.unreadCounts[room.name], count > 0 {
                                        Text("\(count)").font(DesignTokens.fontSmall).foregroundStyle(.white)
                                            .padding(.horizontal, DesignTokens.unreadBadgeHorizontal)
                                            .frame(minWidth: DesignTokens.unreadBadgeSize, minHeight: DesignTokens.unreadBadgeSize)
                                            .background(DesignTokens.blue, in: Capsule())
                                    } else {
                                        Circle().fill(DesignTokens.blue).frame(width: DesignTokens.radiusSmall, height: DesignTokens.radiusSmall)
                                    }
                                }
                            }.font(DesignTokens.fontUI).frame(height: DesignTokens.shellRow).contentShape(Rectangle())
                        }.buttonStyle(.plain)
                        if room.name != "pack-main" && state.hoveredRoom == room.name {
                            Menu { Button("重命名") { state.openRoomDialog(rename: room.name) }; Button("归档") { state.archiveRoom(room.name) } } label: { Image(systemName: "ellipsis") }
                                .menuStyle(.borderlessButton).fixedSize().help("房间操作")
                        }
                    }.foregroundStyle(state.currentRoom == room.name ? DesignTokens.blue : DesignTokens.ink)
                        .onHover { inside in state.hoveredRoom = inside ? room.name : nil }
                }
                HStack { Text("成员"); Spacer() }.font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
                ScrollView {
                    VStack(spacing: DesignTokens.shellGap) {
                        ForEach(state.members) { item in
                            Button { state.toggleMember(item.name) } label: {
                                HStack(spacing: DesignTokens.shellGap) {
                                    MemberAvatarView(name: item.name, member: item, size: DesignTokens.shellAvatar, working: state.tasks.contains { $0.assignee == item.name && ["claimed", "working"].contains($0.status) }).id(state.avatarRevision)
                                    Text(item.name).font(DesignTokens.fontUI).lineLimit(1)
                                    Spacer(minLength: 0)
                                    Text(state.memberStatus(item)).font(DesignTokens.fontSmall).foregroundStyle(item.quotaExhausted ? DesignTokens.danger : DesignTokens.subText)
                                }.frame(height: DesignTokens.shellRow).contentShape(Rectangle())
                                    .background(state.claimingMembers.contains(item.name) ? DesignTokens.blue.opacity(DesignTokens.shellSelectionOpacity) : Color.clear, in: RoundedRectangle(cornerRadius: DesignTokens.radiusCard))
                            }.buttonStyle(.plain)
                        }
                    }
                }
                Button { state.memberActionError = ""; state.showAddMember = true } label: {
                    HStack { Image(systemName: "plus"); Text("添加成员"); Spacer() }.font(DesignTokens.fontUI).frame(height: DesignTokens.shellRow)
                }.buttonStyle(.plain)
            }
            Spacer(minLength: DesignTokens.shellGap)
            Button { select("settings") } label: {
                HStack(spacing: DesignTokens.shellGap) { Image(systemName: "gearshape"); if state.showSidebar { Text("设置"); Spacer() } }
                    .font(DesignTokens.fontUI).frame(maxWidth: .infinity, minHeight: DesignTokens.shellRow)
            }.buttonStyle(.plain).help("设置")
            if state.showSidebar {
                VStack(alignment: .leading, spacing: DesignTokens.shellGap) {
                    HStack {
                        Text("周额度"); Spacer()
                        Text(member?.quotaWeekUsedPct.map { "已用 \(Int($0.rounded()))%" } ?? "暂无数据")
                        if let reset = member?.quotaWeekResetAt { Text(QuotaDisplay.resetDate(reset)) }
                    }
                    HStack {
                        Text("附加 token"); Spacer()
                        Text(member?.quotaExtraLeft.map { QuotaDisplay.tokens($0) } ?? "—")
                        Text("不过期")
                    }
                    if let freshness = QuotaDisplay.freshness(member) { Text(freshness) }
                }.font(DesignTokens.fontSmall)
                    .foregroundStyle((member?.quotaIsStale ?? true) ? DesignTokens.quotaStaleText : DesignTokens.ink)
                    .padding(DesignTokens.shellInset)
                    .background(DesignTokens.shellSurface, in: RoundedRectangle(cornerRadius: DesignTokens.radiusCard))
                    .matchedGeometryEffect(id: "quota", in: quotaMotion)
            } else {
                QuotaRingView(usedPct: member?.quotaWeekUsedPct, stale: member?.quotaIsStale ?? true, size: DesignTokens.shellQuota)
                    .matchedGeometryEffect(id: "quota", in: quotaMotion).help("周额度剩余")
            }
            HStack(spacing: DesignTokens.shellGap) {
                MemberAvatarView(name: "owner", member: state.members.first { $0.name == "owner" }, size: DesignTokens.shellAvatar, showStatus: false).id(state.avatarRevision)
                if state.showSidebar { Text("群主").font(DesignTokens.fontUI); Spacer(minLength: 0) }
                Circle().fill(state.connected ? DesignTokens.okGreen : DesignTokens.faintText)
                    .frame(width: DesignTokens.connectionDotSize, height: DesignTokens.connectionDotSize)
                    .help(state.connected ? "中枢在线" : "中枢重连中")
            }.frame(maxWidth: .infinity).padding(.bottom, DesignTokens.shellInset)
        }.padding(.horizontal, DesignTokens.shellInset)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(SidebarMaterial())
    }
}

