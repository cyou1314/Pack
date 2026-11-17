import SwiftUI
import AppKit
import UniformTypeIdentifiers

private enum PackStyle {
    static let accent = Color(red: 27.0 / 255, green: 124.0 / 255, blue: 245.0 / 255)
    static let amber = Color(red: 0.78, green: 0.48, blue: 0.12)
    static let canvas = Color(nsColor: .textBackgroundColor)
    static let surface = Color(nsColor: .controlBackgroundColor)
    static let line = Color.primary.opacity(0.075)
    static func color(_ hex: String?) -> Color {
        guard let hex else { return accent }
        let s = hex.trimmingCharacters(in: CharacterSet.alphanumerics.inverted)
        guard s.count == 6, let n = UInt64(s, radix: 16) else { return accent }
        return Color(red: Double((n >> 16) & 255) / 255, green: Double((n >> 8) & 255) / 255, blue: Double(n & 255) / 255)
    }
    static func status(_ status: String) -> Color {
        switch status {
        case "open", "released": return .gray
        case "claimed", "working": return accent
        case "review": return .orange
        case "done": return .green
        case "blocked", "cancelled": return .red
        default: return .secondary
        }
    }
}

private struct SidebarMaterial: NSViewRepresentable {
    func makeNSView(context: Context) -> NSVisualEffectView {
        let view = NSVisualEffectView()
        view.material = .sidebar
        view.blendingMode = .behindWindow
        view.state = .active
        return view
    }
    func updateNSView(_ nsView: NSVisualEffectView, context: Context) {}
}

private struct CardSurface: ViewModifier {
    var tint: Color = PackStyle.line
    func body(content: Content) -> some View {
        content.background(PackStyle.surface, in: RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(tint, lineWidth: 1))
            .shadow(color: .black.opacity(0.035), radius: 5, y: 2)
    }
}
private extension View {
    func card(_ color: Color = PackStyle.line) -> some View { modifier(CardSurface(tint: color)) }
}

private struct Pill: View {
    let title: String
    var color: Color = .secondary
    var body: some View {
        Text(title).font(.system(size: 11, weight: .semibold))
            .foregroundStyle(color).padding(.horizontal, 8).padding(.vertical, 4)
            .background(color.opacity(0.12), in: Capsule())
    }
}

private enum QuotaDisplay {
    private static func date(_ timestamp: Double, format: String) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "zh_CN")
        formatter.timeZone = .current
        formatter.dateFormat = format
        return formatter.string(from: Date(timeIntervalSince1970: timestamp))
    }

    static func resetDate(_ timestamp: Double) -> String { date(timestamp, format: "M月d日") }
    static func updatedDate(_ timestamp: Double) -> String { date(timestamp, format: "MM/dd HH:mm") }
    static func tokens(_ value: Int) -> String {
        ByteCountFormatter.string(fromByteCount: Int64(value), countStyle: .decimal)
            .replacingOccurrences(of: "B", with: "")
            .trimmingCharacters(in: .whitespaces)
    }

    static func freshness(_ member: MemberInfo?) -> String? {
        guard let member else { return nil }
        if member.quotaUpdatedAt == nil {
            return member.quotaWeekUsedPct != nil || member.quotaExtraLeft != nil ? "数据时间未知" : nil
        }
        return member.quotaIsStale ? "已过期" : nil
    }
}

private struct MemberAvatarView: View {
    let name: String
    let member: MemberInfo?
    var size: CGFloat = 34
    var showStatus = true

    private var designedImage: NSImage? {
        guard name == "dispatcher" || name == "absent",
              let url = Bundle.main.url(forResource: name, withExtension: "webp", subdirectory: "avatars") else { return nil }
        return NSImage(contentsOf: url)
    }

    var body: some View {
        let avatar = member?.avatar
        let color = DesignTokens.avatarPalettes[avatar?.palette ?? ""] ?? PackStyle.color(avatar?.c1)
        let background = DesignTokens.avatarBackgrounds[avatar?.bg ?? ""] ?? PackStyle.color(avatar?.c2)
        ZStack(alignment: .bottomTrailing) {
            Group {
                if let image = designedImage {
                    Image(nsImage: image).resizable().scaledToFill().clipShape(Circle())
                } else {
                    ParameterAvatar(species: avatar?.species ?? "fox", color: color, background: background, accessory: avatar?.accessory ?? "none")
                }
            }.frame(width: size, height: size).clipShape(Circle())
            if showStatus {
                Circle().fill(member?.isOnline == true ? Color.green : Color.gray)
                    .frame(width: max(8, size * 0.27), height: max(8, size * 0.27))
                    .overlay(Circle().stroke(PackStyle.canvas, lineWidth: 2))
                    .offset(x: 1, y: 1)
            }
        }.frame(width: size, height: size)
    }
}

/// Local vector placeholder driven by the member's registered avatar combination.
private struct ParameterAvatar: View {
    let species: String
    let color: Color
    let background: Color
    let accessory: String
    private var pointed: Bool { ["fox", "wolf", "cat", "tiger", "owl", "raccoon"].contains(species) }
    var body: some View {
        Canvas { context, size in
            let w = size.width, h = size.height
            func rect(_ x: CGFloat, _ y: CGFloat, _ width: CGFloat, _ height: CGFloat) -> CGRect {
                CGRect(x: x*w, y: y*h, width: width*w, height: height*h)
            }
            context.fill(Path(ellipseIn: rect(0, 0, 1, 1)), with: .color(background))
            if pointed {
                var ears = Path()
                ears.move(to: CGPoint(x: 0.18*w, y: 0.48*h)); ears.addLine(to: CGPoint(x: 0.20*w, y: 0.14*h)); ears.addLine(to: CGPoint(x: 0.43*w, y: 0.32*h))
                ears.move(to: CGPoint(x: 0.57*w, y: 0.32*h)); ears.addLine(to: CGPoint(x: 0.80*w, y: 0.14*h)); ears.addLine(to: CGPoint(x: 0.82*w, y: 0.48*h))
                context.fill(ears, with: .color(color))
            } else {
                context.fill(Path(ellipseIn: rect(0.14, 0.17, 0.27, 0.3)), with: .color(color))
                context.fill(Path(ellipseIn: rect(0.59, 0.17, 0.27, 0.3)), with: .color(color))
            }
            context.fill(Path(ellipseIn: rect(0.16, 0.25, 0.68, 0.63)), with: .color(color))
            context.fill(Path(ellipseIn: rect(0.31, 0.57, 0.38, 0.22)), with: .color(.white.opacity(0.75)))
            for x: CGFloat in [0.34, 0.61] { context.fill(Path(ellipseIn: rect(x, 0.47, 0.06, 0.07)), with: .color(DesignTokens.ink)) }
            context.fill(Path(ellipseIn: rect(0.47, 0.61, 0.06, 0.05)), with: .color(DesignTokens.ink))
            if accessory == "glasses" || accessory == "headset" {
                for x: CGFloat in [0.27, 0.54] { context.stroke(Path(ellipseIn: rect(x, 0.41, 0.19, 0.19)), with: .color(DesignTokens.ink), lineWidth: max(1, w * 0.025)) }
            } else if accessory != "none" {
                context.fill(Path(ellipseIn: rect(0.67, 0.18, 0.15, 0.15)), with: .color(DesignTokens.blue))
            }
        }.accessibilityLabel("参数化头像：\(species)")
    }
}

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
                        }
                    }
                }.frame(maxWidth: .infinity, maxHeight: .infinity)
                if state.selection == "room" {
                    Composer(state: state)
                        .disabled(!state.connected)
                        .opacity(state.connected ? 1 : DesignTokens.connectionDisabledOpacity)
                }
            }.frame(maxWidth: .infinity, maxHeight: .infinity).background(PackStyle.canvas)
        }
        .background(PackStyle.canvas)
        .sheet(isPresented: $state.showCreateTask) { CreateTaskView(state: state) }
        .sheet(isPresented: $state.showRoomDialog) { RoomNameDialog(state: state) }
        .sheet(isPresented: $state.showAddMember) { AddMemberView(state: state) }
        .sheet(isPresented: $state.showMemberLogin) { MemberLoginShell(state: state) }
        .sheet(isPresented: $state.showCatalog) { CatalogView(state: state) }
        .sheet(isPresented: $state.showArtifactPreview) { ArtifactPreview(state: state) }
        .sheet(isPresented: $state.showOnboarding) { OnboardingView(state: state) }
        .alert("移除成员", isPresented: $state.showRemoveMember) {
            Button("取消", role: .cancel) { }
            Button("确认移除", role: .destructive) { state.removeSelectedMember() }
        } message: {
            Text("移除后在跑任务回池；消息与交活保留并标记已移除；头像组合释放。此操作会撤销该成员访问。")
        }
        .onAppear { state.start() }
        .preferredColorScheme(.light)
        .animation(DesignTokens.animFast, value: state.connectionFailed)
        .frame(minWidth: DesignTokens.shellMinWidth, minHeight: DesignTokens.shellMinHeight)
    }
}

private struct Sidebar: View {
    @ObservedObject var state: AppState
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
                if state.showSidebar { Text(title); Spacer(minLength: 0) }
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
                                    MemberAvatarView(name: item.name, member: item, size: DesignTokens.shellAvatar)
                                    Text(item.name).font(DesignTokens.fontUI).lineLimit(1)
                                    Spacer(minLength: 0)
                                    Text(state.memberStatus(item)).font(DesignTokens.fontSmall).foregroundStyle(item.quotaExhausted ? DesignTokens.danger : DesignTokens.subText)
                                }.frame(height: DesignTokens.shellRow).contentShape(Rectangle())
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
            } else {
                QuotaRingView(usedPct: member?.quotaWeekUsedPct, stale: member?.quotaIsStale ?? true, size: DesignTokens.shellQuota).help("周额度剩余")
            }
            HStack(spacing: DesignTokens.shellGap) {
                MemberAvatarView(name: "owner", member: state.members.first { $0.name == "owner" }, size: DesignTokens.shellAvatar, showStatus: false)
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

private struct RoomNameDialog: View {
    @ObservedObject var state: AppState
    var body: some View {
        VStack(alignment: .leading, spacing: DesignTokens.shellInset) {
            Text(state.roomRenameFrom == nil ? "新对话" : "重命名房间").font(DesignTokens.fontTitle)
            TextField("房间名称", text: $state.roomDialogName).textFieldStyle(.roundedBorder).onSubmit { state.saveRoom() }
            Text("名称不能含空格或斜杠，最多 40 字。新房间是一段独立对话。")
                .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
            if !state.roomDialogError.isEmpty { Text(state.roomDialogError).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.danger) }
            HStack { Spacer(); Button("取消") { state.showRoomDialog = false }; Button(state.roomRenameFrom == nil ? "创建" : "保存") { state.saveRoom() }.buttonStyle(.borderedProminent).tint(DesignTokens.blue) }
        }.padding(DesignTokens.chatInset).frame(width: DesignTokens.roomDialogWidth)
    }
}

private struct CenterHeader: View {
    @ObservedObject var state: AppState
    var body: some View {
        HStack(spacing: DesignTokens.shellGap) {
            Text(state.selection == "tasks" ? "任务板" : state.selection == "members" ? "成员" : state.selection == "settings" ? "设置" : state.selection == "approvals" ? "收件箱" : state.currentRoom).font(DesignTokens.shellHeaderTitle)
            Spacer()
            if !state.pendingApprovals.isEmpty { Pill(title: "待批准 \(state.pendingApprovals.count)", color: PackStyle.amber) }
        }.padding(.horizontal, DesignTokens.shellHeaderInset).frame(height: DesignTokens.shellHeaderHeight)
    }
}

private struct RoomView: View {
    @ObservedObject var state: AppState
    private var visibleMessages: [RoomMessage] { state.messages.filter { $0.kind != "task_card" && $0.kind != "report" } }
    var body: some View {
        VStack(spacing: 0) {
        if !state.roomLoading && !state.messages.isEmpty, let summary = state.roomSummary {
            SummaryCardView(summary: summary, expanded: state.summaryExpanded) {
                withAnimation(DesignTokens.animFast) { state.summaryExpanded.toggle() }
            }
            .frame(maxWidth: DesignTokens.chatContentWidth)
            .frame(maxWidth: .infinity)
            .padding(.horizontal, DesignTokens.chatInset)
            .padding(.top, DesignTokens.detailPadding)
        }
        ScrollViewReader { proxy in
        ScrollView {
            LazyVStack(spacing: DesignTokens.chatSpacing) {
                if state.roomLoading {
                    ProgressView("正在加载对话…").font(DesignTokens.fontSmall).frame(maxWidth: .infinity)
                } else if !state.connected {
                    Text("暂时无法加载群聊").font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText).frame(maxWidth: .infinity)
                } else if state.messages.isEmpty {
                    EmptyRoomView(state: state)
                }
                ForEach(visibleMessages) { message in
                    if let boundary = state.unreadBoundary, visibleMessages.first(where: { $0.id > boundary })?.id == message.id {
                        HStack { Rectangle().fill(DesignTokens.hairline).frame(height: DesignTokens.boardLine); Text("未读消息").font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText); Rectangle().fill(DesignTokens.hairline).frame(height: DesignTokens.boardLine) }
                    }
                    MessageOperations(state: state, message: message)
                        .overlay(RoundedRectangle(cornerRadius: DesignTokens.radiusCard).stroke(state.highlightedMessageId == message.id ? DesignTokens.blue : Color.clear, lineWidth: DesignTokens.boardHighlightLine).allowsHitTesting(false))
                        .id(message.id)
                }
                if let pending = state.sendingMessage, state.sendingRoom == state.currentRoom {
                    HStack { Spacer(minLength: 0); VStack(alignment: .trailing, spacing: DesignTokens.chatMetadataSpacing) {
                        if let reply = state.sendingReply { ReplyQuoteView(reply: reply) }
                        Text(pending).font(DesignTokens.fontUI).foregroundStyle(.white)
                            .padding(.horizontal, DesignTokens.chatBubbleHorizontal).padding(.vertical, DesignTokens.chatBubbleVertical)
                            .background(DesignTokens.ink, in: RoundedRectangle(cornerRadius: DesignTokens.radiusBubble))
                        Text("发送中…").font(DesignTokens.chatReceiptFont).foregroundStyle(DesignTokens.subText)
                    }.frame(maxWidth: DesignTokens.chatBubbleWidth, alignment: .trailing) }
                }
            }.frame(maxWidth: DesignTokens.chatContentWidth).frame(maxWidth: .infinity)
                .padding(.horizontal, DesignTokens.chatInset).padding(.vertical, DesignTokens.chatVerticalInset)
        }.onAppear {
            if let id = state.highlightedMessageId { proxy.scrollTo(id, anchor: .center) }
        }.onChange(of: state.highlightedMessageId) { id in
            if let id { withAnimation(DesignTokens.animStd) { proxy.scrollTo(id, anchor: .center) } }
        }
        }
        }
    }
}

private struct MessageOperations: View {
    @ObservedObject var state: AppState
    let message: RoomMessage
    private var usesBubble: Bool {
        if message.kind == "system" { return false }
        if ["approval_request", "task_card", "report"].contains(message.kind),
           let task = state.tasks.first(where: { $0.id == message.taskId }) {
            return message.kind == "report" && task.result == nil && task.envelope == nil
        }
        return true
    }
    var body: some View {
        HStack(alignment: .center, spacing: DesignTokens.messageActionSpacing) {
            MessageRow(state: state, message: message)
            if !usesBubble && state.hoveredMessageId == message.id {
                MessageActionButtons(state: state, message: message)
            }
        }.onHover { inside in
            withAnimation(DesignTokens.animFast) {
                if inside { state.hoveredMessageId = message.id }
                else if state.hoveredMessageId == message.id { state.hoveredMessageId = nil }
            }
        }
    }
}

private struct MessageActionButtons: View {
    @ObservedObject var state: AppState
    let message: RoomMessage
    private var canModify: Bool { message.author == "owner" && message.kind == "chat" && !message.deleted }
    var body: some View {
        HStack(spacing: DesignTokens.messageActionSpacing) {
            Button {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(message.deleted ? "（已删除）" : ReplyCodec.decode(message.body).body, forType: .string)
            } label: { Image(systemName: "doc.on.doc") }.help("复制")
            Button { state.replyTo(message) } label: { Image(systemName: "arrowshape.turn.up.left") }.help("回复")
            if canModify {
                Button { state.editMessage(message) } label: { Image(systemName: "pencil") }.help("编辑")
                Button { state.deleteMessage(message) } label: { Image(systemName: "trash") }.foregroundStyle(DesignTokens.danger).help("删除")
            }
        }.font(DesignTokens.fontSmall).buttonStyle(.plain).transition(.opacity)
    }
}

private struct MessageRow: View {
    @ObservedObject var state: AppState
    @FocusState private var editFocused: Bool
    let message: RoomMessage
    var task: PackTask? { state.tasks.first { $0.id == message.taskId } }
    var member: MemberInfo? { state.members.first { $0.name == message.author } }
    var isOwner: Bool { message.author == "owner" }
    private var decoded: (body: String, reply: ReplySnapshot?) { message.deleted ? ("（已删除）", nil) : ReplyCodec.decode(message.body) }
    var acknowledged: Bool {
        guard let index = state.messages.firstIndex(where: { $0.id == message.id }) else { return false }
        return state.messages.dropFirst(index + 1).contains { $0.kind == "task_card" || $0.author == "dispatcher" }
    }
    var body: some View {
        if message.kind == "system" {
            Text(message.body).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText).frame(maxWidth: .infinity)
        } else if message.kind == "approval_request", let t = task {
            ApprovalCard(state: state, message: message, task: t)
        } else if message.kind == "task_card", let t = task {
            TaskCard(state: state, task: t)
        } else if message.kind == "report", let t = task, t.result != nil || t.envelope != nil {
            DeliveryCard(state: state, task: t)
        } else {
            HStack(alignment: .top, spacing: DesignTokens.chatRowSpacing) {
                if isOwner { Spacer(minLength: 0) } else { MemberAvatarView(name: message.author, member: member, size: DesignTokens.chatAvatar, showStatus: false) }
                VStack(alignment: isOwner ? .trailing : .leading, spacing: DesignTokens.chatMetadataSpacing) {
                    if !isOwner { Text(message.author).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText) }
                    if let reply = decoded.reply { ReplyQuoteView(reply: reply) }
                    // 长消息折叠位：保留完整正文，待 B1-8 接入展开状态与阈值。
                    HStack(alignment: .center, spacing: DesignTokens.messageActionSpacing) {
                    Group {
                        if state.editingMessageId == message.id {
                            TextField("编辑消息", text: $state.editingMessageText)
                                .textFieldStyle(.plain).focused($editFocused)
                                .onAppear { editFocused = true }
                                .onSubmit { state.saveMessageEdit(message) }
                                .onExitCommand { state.editingMessageId = nil; state.editingMessageText = "" }
                        } else {
                            Text(decoded.body).textSelection(.enabled)
                                .lineLimit((decoded.body.count > DesignTokens.longMessageThreshold || decoded.body.filter({ $0 == "\n" }).count >= DesignTokens.longMessageLines) && !state.expandedMessages.contains(message.id) ? DesignTokens.longMessageLines : nil)
                        }
                    }.font(DesignTokens.fontUI)
                        .foregroundStyle(isOwner ? Color.white : DesignTokens.ink)
                        .padding(.horizontal, DesignTokens.chatBubbleHorizontal).padding(.vertical, DesignTokens.chatBubbleVertical)
                        .background(isOwner ? DesignTokens.ink : DesignTokens.bubbleAI, in: RoundedRectangle(cornerRadius: DesignTokens.radiusBubble))
                        .fixedSize(horizontal: false, vertical: true)
                        if state.hoveredMessageId == message.id {
                            MessageActionButtons(state: state, message: message)
                        }
                    }
                    if message.edited && !message.deleted { Text("已编辑").font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText) }
                    if decoded.body.count > DesignTokens.longMessageThreshold || decoded.body.filter({ $0 == "\n" }).count >= DesignTokens.longMessageLines {
                        Button(state.expandedMessages.contains(message.id) ? "收起" : "展开全文") {
                            if state.expandedMessages.contains(message.id) { state.expandedMessages.remove(message.id) } else { state.expandedMessages.insert(message.id) }
                        }.buttonStyle(.plain).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.blue)
                    }
                    if isOwner {
                        Text(acknowledged ? "✓✓ 已认领" : "✓✓ 已送达")
                            .font(DesignTokens.chatReceiptFont).foregroundStyle(acknowledged ? DesignTokens.blue : DesignTokens.subText)
                    }
                }.frame(maxWidth: DesignTokens.chatBubbleWidth, alignment: isOwner ? .trailing : .leading)
                if !isOwner { Spacer(minLength: 0) }
            }
        }
    }
}

private struct ReplyQuoteView: View {
    let reply: ReplySnapshot
    var body: some View {
        HStack(alignment: .center, spacing: DesignTokens.replyPadding) {
            RoundedRectangle(cornerRadius: DesignTokens.radiusSmall).fill(DesignTokens.blue).frame(width: DesignTokens.replyBarWidth)
            VStack(alignment: .leading, spacing: DesignTokens.memberListTextSpacing) {
                Text(reply.author).font(DesignTokens.summarySectionFont).foregroundStyle(DesignTokens.blue)
                Text(reply.text.replacingOccurrences(of: "\n", with: " ")).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText).lineLimit(1)
            }
        }.fixedSize(horizontal: false, vertical: true)
            .padding(DesignTokens.replyPadding)
            .background(DesignTokens.blue.opacity(DesignTokens.replyBackgroundOpacity), in: RoundedRectangle(cornerRadius: DesignTokens.radiusSmall))
    }
}

private struct Composer: View {
    @ObservedObject var state: AppState
    var body: some View {
        VStack(alignment: .leading, spacing: DesignTokens.composerSpacing) {
                if let reply = state.replyByRoom[state.currentRoom] {
                    HStack(spacing: DesignTokens.shellGap) {
                        ReplyQuoteView(reply: reply)
                        Spacer(minLength: 0)
                        Button { state.replyByRoom.removeValue(forKey: state.currentRoom) } label: { Image(systemName: "xmark") }
                            .buttonStyle(.plain).font(DesignTokens.fontSmall).help("取消回复")
                    }
                }
                if state.mentionPopupVisible {
                    VStack(alignment: .leading, spacing: 0) {
                        ForEach(state.mentionCandidates) { member in
                            Button {
                                state.completeMention(member)
                            } label: {
                                HStack(spacing: DesignTokens.shellGap) {
                                    MemberAvatarView(name: member.name, member: member, size: DesignTokens.mentionCandidateAvatar, showStatus: false)
                                    Text(member.name).font(DesignTokens.fontUI)
                                    Spacer()
                                    Text(state.mentionStatus(member)).font(DesignTokens.fontSmall)
                                        .foregroundStyle(member.quotaExhausted ? DesignTokens.danger : DesignTokens.subText)
                                }.padding(.horizontal, DesignTokens.mentionCandidateHorizontal).padding(.vertical, DesignTokens.mentionCandidateVertical).contentShape(Rectangle())
                            }.buttonStyle(.plain)
                        }
                    }.background(Color(nsColor: .controlBackgroundColor))
                }
                MentionTextEditor(state: state)
            HStack(spacing: DesignTokens.composerSpacing) {
                Button { state.showAddMenu.toggle() } label: {
                    Image(systemName: "plus").font(DesignTokens.fontTitle).foregroundStyle(.white)
                        .frame(width: DesignTokens.menuButtonSize, height: DesignTokens.menuButtonSize)
                        .background(DesignTokens.ink, in: Circle())
                }.buttonStyle(.plain).help("添加或派活")
                .overlay(alignment: .bottomLeading) {
                    if state.showAddMenu {
                    VStack(spacing: 0) {
                        menuRow("添加文件…", icon: "doc.badge.plus") { chooseFiles(imagesOnly: false) }
                        menuRow("添加照片…", icon: "photo") { chooseFiles(imagesOnly: true) }
                        menuRow("选择技能", icon: "sparkles", arrow: true) { state.loadCatalog("技能") }
                        menuRow("选择模板", icon: "rectangle.split.2x2", arrow: true) { state.loadCatalog("模板") }
                        menuRow(state.showInlineTask ? "关闭派活" : "派活给 absent", icon: "at", arrow: true) { state.newAssignee = "absent"; state.showInlineTask.toggle(); state.inlineTaskError = ""; state.composerFocusRequest += 1 }
                    }.padding(DesignTokens.shellGap).frame(width: DesignTokens.composerMenuWidth)
                        .background(DesignTokens.shellSurface, in: RoundedRectangle(cornerRadius: DesignTokens.radiusCard))
                        .overlay(RoundedRectangle(cornerRadius: DesignTokens.radiusCard).stroke(DesignTokens.hairline, lineWidth: DesignTokens.boardLine))
                        .shadow(color: .black.opacity(DesignTokens.shellSelectionOpacity), radius: DesignTokens.composerMenuShadow)
                        .offset(y: -DesignTokens.menuButtonSize - DesignTokens.shellGap)
                    }
                }
                .zIndex(1)
                Button { state.newNeedsApproval.toggle() } label: {
                    Text("需批准 · \(state.newNeedsApproval ? "开" : "关")")
                        .font(DesignTokens.fontSmall).foregroundStyle(state.newNeedsApproval ? DesignTokens.blue : DesignTokens.subText)
                        .padding(.horizontal, DesignTokens.composerPillHorizontal).padding(.vertical, DesignTokens.composerPillVertical)
                        .background(DesignTokens.bubbleAI, in: Capsule())
                }.buttonStyle(.plain).help("切换是否需要批准")
                Spacer()
                if state.showInlineTask {
                    Text("指派：\(state.newAssignee)").font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
                }
                Button { state.submitComposer() } label: {
                    Image(systemName: "arrow.up").font(DesignTokens.fontTitle).foregroundStyle(.white)
                        .frame(width: DesignTokens.composerSend, height: DesignTokens.composerSend)
                        .background(DesignTokens.blue, in: Circle())
                }.buttonStyle(.plain)
                    .disabled(state.showInlineTask && (state.inlineTaskSending || state.draftMessage.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !state.attachments.isEmpty))
                    .help(state.showInlineTask ? "建卡派活" : "发送")
            }
            if !state.inlineTaskError.isEmpty {
                Text(state.inlineTaskError).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.danger)
            }
            if !state.attachments.isEmpty {
                Text("已选择 \(state.attachments.count) 个本地文件；中枢暂不支持附件上传，请移除后发送。")
                    .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.danger)
                Button("移除所选文件") { state.attachments = [] }.font(DesignTokens.fontSmall)
            }
        }.fixedSize(horizontal: false, vertical: true)
            .padding(DesignTokens.composerPadding).background {
            RoundedRectangle(cornerRadius: DesignTokens.composerRadius).fill(PackStyle.surface)
                .overlay(RoundedRectangle(cornerRadius: DesignTokens.composerRadius).stroke(PackStyle.line))
                .shadow(color: .black.opacity(DesignTokens.composerShadowOpacity), radius: DesignTokens.composerShadowRadius, y: DesignTokens.composerShadowOffset)
        }.frame(maxWidth: DesignTokens.composerWidth).frame(maxWidth: .infinity)
            .padding(.horizontal, DesignTokens.chatInset).padding(.top, DesignTokens.composerSpacing)
    }
    private func menuRow(_ title: String, icon: String, arrow: Bool = false, action: @escaping () -> Void) -> some View {
        Button { state.showAddMenu = false; action() } label: {
            HStack(spacing: DesignTokens.shellGap) {
                Image(systemName: icon).frame(width: DesignTokens.composerMenuIcon).foregroundStyle(DesignTokens.subText)
                Text(title).foregroundStyle(DesignTokens.ink)
                Spacer()
                if arrow { Image(systemName: "chevron.right").font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.faintText) }
            }.font(DesignTokens.fontUI).frame(height: DesignTokens.composerMenuRow).contentShape(Rectangle())
        }.buttonStyle(.plain)
    }
    private func chooseFiles(imagesOnly: Bool) {
        let picker = NSOpenPanel()
        picker.allowsMultipleSelection = true
        picker.canChooseDirectories = false
        if imagesOnly { picker.allowedContentTypes = [.image] }
        picker.begin { result in if result == .OK { state.attachments = picker.urls } }
    }
}

private struct CatalogView: View {
    @ObservedObject var state: AppState
    var body: some View {
        VStack(alignment: .leading, spacing: DesignTokens.settingsGap) {
            Text("选择\(state.catalogKind)").font(DesignTokens.fontTitle)
            if !state.catalogNote.isEmpty { Text(state.catalogNote).font(DesignTokens.fontSmall) }
            if state.catalogKind == "模板" { TextField("模板参数（如需要）", text: $state.catalogArgument).textFieldStyle(.roundedBorder) }
            ScrollView {
                ForEach(Array(state.catalogItems.enumerated()), id: \.offset) { _, item in
                    let name = item["name"] as? String ?? "?"
                    Button(name) {
                        if state.catalogKind == "技能" { state.draftMessage += "【技能：\(name)】"; state.showCatalog = false }
                        else { state.fireTemplate(name) }
                    }.buttonStyle(.plain).frame(maxWidth: .infinity, alignment: .leading).padding(DesignTokens.detailPadding)
                }
            }
            Button("关闭") { state.showCatalog = false }
        }.padding(DesignTokens.settingsInset).frame(width: DesignTokens.memberLoginWidth, height: DesignTokens.memberLoginHeight)
    }
}

private struct EmptyRoomView: View {
    @ObservedObject var state: AppState
    var body: some View {
        VStack(spacing: DesignTokens.memberGap) {
            if let url = Bundle.main.url(forResource: "mark", withExtension: "png"), let mark = NSImage(contentsOf: url) {
                Image(nsImage: mark).resizable().scaledToFit().frame(width: DesignTokens.emptyMarkSize, height: DesignTokens.emptyMarkSize)
            }
            Text("新对话").font(DesignTokens.shellTitle)
            Text("在下方输入，或点一个快捷开始").font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
            shortcut("查资料", draft: "让 absent 查个资料：")
            shortcut("生成图", draft: "让 absent 生成一张图：")
            shortcut("巡检额度", draft: "巡检一下各成员额度")
        }.frame(maxWidth: .infinity, minHeight: DesignTokens.emptyRoomHeight)
    }
    private func shortcut(_ title: String, draft: String) -> some View {
        Button { state.draftMessage = draft } label: {
            Text(title).font(DesignTokens.fontUI).frame(maxWidth: DesignTokens.emptyShortcutWidth, minHeight: DesignTokens.emptyShortcutHeight)
                .overlay(RoundedRectangle(cornerRadius: DesignTokens.radiusCard).stroke(DesignTokens.hairline, lineWidth: DesignTokens.boardLine))
        }.buttonStyle(.plain)
    }
}

private struct TaskCard: View {
    @ObservedObject var state: AppState
    let task: PackTask
    private var expanded: Bool { state.expandedChatTaskId == task.id }
    var body: some View {
        VStack(alignment: .leading, spacing: DesignTokens.detailSpacing) {
            Button { state.toggleChatTask(task.id) } label: {
                HStack(alignment: .top, spacing: DesignTokens.detailSpacing) {
                    VStack(alignment: .leading, spacing: DesignTokens.detailSpacing) {
                        Text("任务 #\(task.id) · \(task.title)").font(DesignTokens.fontTitle)
                        Text(task.body).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText).lineLimit(expanded ? nil : 2)
                    }
                    Spacer(minLength: 0)
                    Text(task.statusLabel).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
                    Image(systemName: expanded ? "chevron.up" : "chevron.down")
                }.contentShape(Rectangle())
            }.buttonStyle(.plain)
            if expanded { ChatTaskDetail(state: state, task: task).transition(.opacity.combined(with: .move(edge: .top))) }
        }.padding(DesignTokens.detailPadding)
            .background(DesignTokens.shellSurface, in: RoundedRectangle(cornerRadius: DesignTokens.radiusCard))
            .overlay(RoundedRectangle(cornerRadius: DesignTokens.radiusCard).stroke(DesignTokens.hairline, lineWidth: DesignTokens.boardLine))
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct ChatTaskDetail: View {
    @ObservedObject var state: AppState
    let task: PackTask
    private func json(_ value: Any) -> String {
        guard JSONSerialization.isValidJSONObject(value), let data = try? JSONSerialization.data(withJSONObject: value, options: [.prettyPrinted, .sortedKeys]), let text = String(data: data, encoding: .utf8) else { return String(describing: value) }
        return text
    }
    var body: some View {
        VStack(alignment: .leading, spacing: DesignTokens.detailSpacing) {
            Divider()
            Text("执行者：\(task.assignee ?? "未指派") · 尝试次数：\(task.attempts)").font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
            if let lease = task.leaseExpires { Text("租约到期：\(Date(timeIntervalSince1970: lease).formatted())").font(DesignTokens.fontSmall) }
            Text("五步时间线").font(DesignTokens.fontTitle)
            ForEach([("创建", "task_create"), ("认领", "task_claim"), ("执行", "run_start"), ("交活", "task_complete"), ("验收", "task_review")], id: \.1) { label, action in
                let event = state.taskEvents.last { ($0["action"] as? String) == action }
                let stamp = event.flatMap { optDoubleVal($0["ts"]) }
                HStack {
                    Text(label).font(DesignTokens.fontUI)
                    Spacer()
                    if let stamp { Text(Date(timeIntervalSince1970: stamp).formatted()).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText) }
                    else { Text(state.detailLoadNote.isEmpty ? "暂无记录" : "未确认").font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText) }
                }
            }
            if !state.detailLoadNote.isEmpty { Text(state.detailLoadNote).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText) }
            ForEach(Array(state.taskRuns.enumerated()), id: \.offset) { _, run in
                let start = doubleVal(run["started_at"]); let end = optDoubleVal(run["ended_at"])
                Text("\((run["member"] as? String) ?? "成员") · 第 \(intVal(run["fence"])) 次 · \((run["outcome"] as? String) ?? (run["status"] as? String) ?? "未知")" + (end.map { " · \(Int(max(0, $0 - start))) 秒" } ?? " · 进行中"))
                    .font(DesignTokens.fontSmall)
            }
            Text("交活信封").font(DesignTokens.fontTitle)
            if let note = task.reviewNote, !note.isEmpty { Text("验收意见：\(note)").font(DesignTokens.fontSmall).textSelection(.enabled) }
            if let envelope = task.envelope {
                Text("结论：\(envelope.verdict)\n摘要：\(envelope.summary)").font(DesignTokens.fontUI).textSelection(.enabled)
                Text("产物：\(json(envelope.artifacts))").font(DesignTokens.fontSmall).textSelection(.enabled)
                Text("自检：\(envelope.selfCheck ?? "未提供")").font(DesignTokens.fontSmall).textSelection(.enabled)
                if !envelope.knownIssues.isEmpty { Text("已知问题：\(envelope.knownIssues.joined(separator: "；"))").font(DesignTokens.fontSmall) }
                if !envelope.followups.isEmpty { Text("后续事项：\(envelope.followups.joined(separator: "；"))").font(DesignTokens.fontSmall) }
            } else { Text(task.result ?? "暂无交活信封").font(DesignTokens.fontSmall).textSelection(.enabled) }
            Text("转交记录").font(DesignTokens.fontTitle)
            if state.detailLoadNote.isEmpty && state.taskTransfers.isEmpty { Text("暂无转交记录").font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText) }
            ForEach(Array(state.taskTransfers.enumerated()), id: \.offset) { _, transfer in
                Text("\((transfer["from_member"] as? String) ?? "?") → \((transfer["to_member"] as? String) ?? "?") · \((transfer["status"] as? String) ?? "?") · \((transfer["reason"] as? String) ?? "")").font(DesignTokens.fontSmall).textSelection(.enabled)
            }
            Text("审计记录").font(DesignTokens.fontTitle)
            ForEach(Array(state.taskEvents.enumerated()), id: \.offset) { _, event in
                Text("\((event["action"] as? String) ?? "事件") · \((event["actor"] as? String) ?? "") · \((event["detail"] as? String) ?? "")").font(DesignTokens.fontSmall).textSelection(.enabled)
            }
        }.frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct ApprovalCard: View {
    @ObservedObject var state: AppState
    let message: RoomMessage
    let task: PackTask
    var pending: Bool { state.pendingApprovals.contains { $0.id == message.id } }
    var reason: Binding<String> { Binding(get: { state.rejectReasons[task.id] ?? "" }, set: { state.rejectReasons[task.id] = $0 }) }
    private var decision: RoomMessage? { state.approvalDecision(for: message) }
    var body: some View {
        VStack(alignment: .leading, spacing: DesignTokens.detailSpacing) {
            if pending {
                Text("待批准 · 任务 #\(task.id)").font(DesignTokens.fontTitle).foregroundStyle(DesignTokens.amber)
                Text(task.title).font(DesignTokens.fontTitle)
                Text(message.body).font(DesignTokens.fontSmall).textSelection(.enabled)
                TextField("拒绝原因（可选）", text: reason).textFieldStyle(.roundedBorder).font(DesignTokens.fontSmall)
                HStack(spacing: DesignTokens.detailSpacing) {
                    Button("仅这一次") { state.approveTask(task.id) }.buttonStyle(.borderedProminent).tint(DesignTokens.blue)
                    Button("总是允许") {}.buttonStyle(.bordered).disabled(true).help("持久规则尚未接入")
                    Button("拒绝") { state.rejectTask(task.id) }.buttonStyle(.bordered).foregroundStyle(DesignTokens.danger)
                }.font(DesignTokens.fontSmall)
            } else if let decision {
                Text(decision.body.hasPrefix("批准") ? (decision.body.contains("规则库") ? "✓ 已批准 · 规则自动通过" : "✓ 已批准 · 仅这一次") : "已拒绝").font(DesignTokens.fontSmall)
            } else {
                Text("已结束 · 未找到决议记录").font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
            }
        }.padding(DesignTokens.detailPadding)
            .background(DesignTokens.approvalBackground, in: RoundedRectangle(cornerRadius: DesignTokens.radiusCard))
            .overlay(RoundedRectangle(cornerRadius: DesignTokens.radiusCard).stroke(DesignTokens.approvalBorder, lineWidth: DesignTokens.boardLine))
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct DeliveryCard: View {
    @ObservedObject var state: AppState
    let task: PackTask
    @FocusState private var reasonFocused: Bool
    var body: some View {
        VStack(alignment: .leading, spacing: DesignTokens.detailSpacing) {
            HStack { Text("交付成果 · #\(task.id)").font(DesignTokens.fontTitle); Spacer(); Text(task.statusLabel).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText) }
            Text(task.result ?? task.envelope?.summary ?? "").font(DesignTokens.fontUI).textSelection(.enabled)
            if let artifacts = task.envelope?.artifacts, !artifacts.isEmpty {
                ArtifactGroup(state: state, taskId: task.id, artifacts: artifacts)
            }
            if let reason = state.reviewNotes[task.id], task.status != "review" { Text("已打回：\(reason)").font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.danger) }
            if task.status == "review" {
                if state.reviewInputTaskId == task.id {
                    TextField("填写打回理由，回车提交", text: $state.reviewInput)
                        .focused($reasonFocused).onSubmit { state.reviewTask(task.id, approve: false, note: state.reviewInput) }
                        .onExitCommand { state.reviewInputTaskId = nil; state.reviewInput = "" }
                } else {
                    HStack {
                        Button("通过") { state.reviewTask(task.id, approve: true) }.buttonStyle(.borderedProminent).tint(DesignTokens.ink)
                        Button("打回") { state.reviewInput = ""; state.reviewInputTaskId = task.id; reasonFocused = true }.buttonStyle(.plain)
                    }.font(DesignTokens.fontSmall)
                }
            }
            if state.lastActionNote.hasPrefix("操作未完成") || state.lastActionNote == "请填写打回理由" { Text(state.lastActionNote).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.danger) }
        }.padding(DesignTokens.detailPadding).frame(maxWidth: .infinity, alignment: .leading)
            .background(DesignTokens.shellSurface, in: RoundedRectangle(cornerRadius: DesignTokens.radiusCard))
            .overlay(RoundedRectangle(cornerRadius: DesignTokens.radiusCard).stroke(DesignTokens.hairline, lineWidth: DesignTokens.boardLine))
    }
}

private struct ArtifactGroup: View {
    @ObservedObject var state: AppState
    let taskId: Int
    let artifacts: [[String: Any]]
    private func url(_ item: [String: Any]) -> URL? {
        if let path = item["path"] as? String { return URL(fileURLWithPath: path) }
        if let text = item["url"] as? String, let u = URL(string: text), ["https", "http"].contains(u.scheme?.lowercased() ?? "") { return u }
        return nil
    }
    var body: some View {
        LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: DesignTokens.detailSpacing) {
            ForEach(Array(artifacts.enumerated()), id: \.offset) { index, item in
                if state.selectedArtifacts[taskId] == nil || state.selectedArtifacts[taskId] == index {
                    if let target = url(item) {
                        VStack(alignment: .leading, spacing: DesignTokens.detailSpacing) {
                            if (item["kind"] as? String)?.lowercased().contains("image") == true {
                                Button { state.artifactPreviewURL = target; state.showArtifactPreview = true } label: {
                                    ArtifactImage(url: target).frame(height: DesignTokens.artifactPreviewSize).clipped()
                                        .overlay(alignment: .bottomTrailing) {
                                            Image(systemName: "arrow.down").foregroundStyle(DesignTokens.ink).padding(DesignTokens.detailPadding)
                                        }
                                }.buttonStyle(.plain)
                            } else {
                                Button { NSWorkspace.shared.open(target) } label: {
                                    Label(target.lastPathComponent, systemImage: "doc").font(DesignTokens.fontSmall)
                                }.buttonStyle(.plain)
                            }
                            if artifacts.count > 1 {
                                Button(state.selectedArtifacts[taskId] == index ? "已选定" : "选这张") { state.selectedArtifacts[taskId] = index }
                                    .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.blue)
                            }
                        }
                    } else { Text("产物引用不可用").font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText) }
                }
            }
        }
    }
}

private struct ArtifactImage: View {
    let url: URL
    var body: some View {
        Group {
            if url.isFileURL {
                if let image = NSImage(contentsOf: url) { Image(nsImage: image).resizable().scaledToFit() }
                else { Text("图片不可用").font(DesignTokens.fontSmall) }
            } else {
                AsyncImage(url: url) { image in image.resizable().scaledToFit() } placeholder: { Text("图片加载中").font(DesignTokens.fontSmall) }
            }
        }.frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

private struct ArtifactPreview: View {
    @ObservedObject var state: AppState
    var body: some View {
        Group { if let url = state.artifactPreviewURL { ArtifactImage(url: url) } }
            .frame(width: DesignTokens.settingsWidth, height: DesignTokens.memberLoginHeight)
    }
}

private struct TaskBoard: View {
    @ObservedObject var state: AppState
    private let groups: [(String, [String])] = [
        ("待认领", ["open", "released"]), ("进行中", ["claimed", "working", "blocked"]),
        ("待验收", ["review"]), ("已完成", ["done"])
    ]
    private var filtered: [PackTask] {
        if state.boardFilter == "全部" { return state.tasks }
        if state.boardFilter == "我派的" { return state.tasks.filter { ["owner", "dispatcher"].contains($0.createdBy ?? "") } }
        return state.tasks.filter { $0.assignee == state.boardFilter }
    }
    var body: some View {
        VStack(alignment: .leading, spacing: DesignTokens.boardGap) {
            HStack(spacing: DesignTokens.boardGap) {
                filter("全部")
                ForEach(state.members.filter { $0.role == "member" }) { member in filter(member.name) }
                filter("我派的")
                Spacer(minLength: 0)
            }.font(DesignTokens.fontUI).frame(height: DesignTokens.boardTabHeight)
            GeometryReader { geometry in
                let width = max(0, (geometry.size.width - DesignTokens.boardGap * 3) / 4)
                HStack(alignment: .top, spacing: DesignTokens.boardGap) {
                    ForEach(groups, id: \.0) { title, statuses in
                        let items = filtered.filter { statuses.contains($0.status) }
                        VStack(alignment: .leading, spacing: DesignTokens.boardGap) {
                            HStack { Text(title).font(DesignTokens.fontTitle); Text("\(items.count)").font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText); Spacer(minLength: 0) }
                            ScrollView(.vertical) {
                                LazyVStack(spacing: DesignTokens.boardGap) {
                                    ForEach(items) { task in BoardTaskCard(state: state, task: task) }
                                }.frame(maxWidth: .infinity)
                            }
                        }.frame(width: width, height: geometry.size.height, alignment: .top)
                    }
                }
            }
        }.padding(DesignTokens.boardInset).frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
    private func filter(_ value: String) -> some View {
        Button { state.boardFilter = value } label: {
            VStack(spacing: 0) {
                Text(value == "全部" || value == "我派的" ? value : "\(value) 的").font(DesignTokens.fontUI)
                    .foregroundStyle(state.boardFilter == value ? DesignTokens.ink : DesignTokens.subText)
                    .frame(height: DesignTokens.boardTabHeight)
                Rectangle().fill(state.boardFilter == value ? DesignTokens.ink : Color.clear).frame(height: DesignTokens.boardLine)
            }
        }.buttonStyle(.plain).fixedSize(horizontal: true, vertical: false)
    }
}

private struct BoardTaskCard: View {
    @ObservedObject var state: AppState
    let task: PackTask
    private var member: MemberInfo? { state.members.first { $0.name == task.assignee } }
    private var tag: String? {
        guard task.title.hasPrefix("["), let end = task.title.firstIndex(of: "]") else { return nil }
        return String(task.title[...end])
    }
    var body: some View {
        VStack(alignment: .leading, spacing: DesignTokens.boardCardGap) {
            HStack(alignment: .top, spacing: DesignTokens.boardCardGap) {
                Text("#\(task.id) \(task.title)").font(DesignTokens.fontTitle).foregroundStyle(task.status == "done" ? DesignTokens.subText : DesignTokens.ink)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
                if let tag { Text(tag).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.faintText).lineLimit(1) }
            }
            Text((task.status == "open" ? (task.reviewNote ?? state.reviewNotes[task.id]) : nil).map { "打回：\($0)" } ?? (task.status == "review" ? (task.envelope?.summary ?? task.result ?? task.body) : task.body))
                .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText).lineLimit(1)
            Rectangle().fill(DesignTokens.hairline).frame(height: DesignTokens.boardLine)
            HStack(spacing: DesignTokens.boardCardGap) {
                if let name = task.assignee {
                    MemberAvatarView(name: name, member: member, size: DesignTokens.boardAvatar, showStatus: false)
                    Text(name).lineLimit(1)
                } else { Text("未指派") }
                Spacer(minLength: 0)
                Text(task.status == "done" ? "已关单" : task.statusLabel)
                    .foregroundStyle(task.status == "blocked" ? DesignTokens.danger : DesignTokens.subText).lineLimit(1)
            }.font(DesignTokens.fontSmall)
            if ["claimed", "working"].contains(task.status) {
                Rectangle().fill(DesignTokens.hairline).frame(height: DesignTokens.boardProgressHeight)
            }

        }.padding(DesignTokens.boardCardInset)
            .background(DesignTokens.shellSurface, in: RoundedRectangle(cornerRadius: DesignTokens.boardCardRadius))
            .overlay(RoundedRectangle(cornerRadius: DesignTokens.boardCardRadius).stroke(DesignTokens.hairline, lineWidth: DesignTokens.boardLine))
            .contentShape(Rectangle())
            .onTapGesture { state.openTaskDetail(task.id) }.help("查看任务详情")
    }
}

private struct ApprovalsView: View {
    @ObservedObject var state: AppState
    private var reviews: [PackTask] { state.tasks.filter { $0.status == "review" } }
    private var offline: [MemberInfo] {
        state.members.filter { member in
            member.role == "member" && member.name != "owner" && member.lastHeartbeat.map { Date().timeIntervalSince1970 - $0 >= DesignTokens.offlineAlertSeconds } == true
        }
    }
    private func jump(_ message: RoomMessage) {
        state.highlightedMessageId = message.id; state.selection = "room"
    }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: DesignTokens.settingsGap) {
                Text("收件箱 · 当前房间").font(DesignTokens.fontTitle)
                Text("待批准 · \(state.pendingApprovals.count)").font(DesignTokens.fontTitle)
                if state.pendingApprovals.isEmpty { Text("暂无待批准").font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText) }
                ForEach(state.pendingApprovals) { message in
                    Button { jump(message) } label: { Label(message.body, systemImage: "checkmark.shield").frame(maxWidth: .infinity, alignment: .leading) }
                        .buttonStyle(.plain).padding(DesignTokens.memberInset).overlay(RoundedRectangle(cornerRadius: DesignTokens.radiusCard).stroke(DesignTokens.hairline))
                }
                Text("交活待验收 · \(reviews.count)").font(DesignTokens.fontTitle)
                if reviews.isEmpty { Text("暂无待验收").font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText) }
                ForEach(reviews) { task in
                    Button { state.openTaskDetail(task.id) } label: { Label("#\(task.id) \(task.title)", systemImage: "doc.text").frame(maxWidth: .infinity, alignment: .leading) }
                        .buttonStyle(.plain)
                        .padding(DesignTokens.memberInset).overlay(RoundedRectangle(cornerRadius: DesignTokens.radiusCard).stroke(DesignTokens.hairline))
                }
                Text("成员掉线超过 10 分钟 · \(offline.count)").font(DesignTokens.fontTitle)
                if offline.isEmpty { Text("暂无掉线成员").font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText) }
                ForEach(offline) { member in
                    Button { state.toggleMember(member.name) } label: { Label("\(member.name) · 已离线", systemImage: "person.crop.circle.badge.exclamationmark").frame(maxWidth: .infinity, alignment: .leading) }
                        .buttonStyle(.plain).padding(DesignTokens.memberInset).overlay(RoundedRectangle(cornerRadius: DesignTokens.radiusCard).stroke(DesignTokens.hairline))
                }
            }.padding(DesignTokens.settingsInset).frame(maxWidth: DesignTokens.settingsWidth, alignment: .leading).frame(maxWidth: .infinity, alignment: .center)
        }
    }
}

private struct SettingsView: View {
    @ObservedObject var state: AppState
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: DesignTokens.settingsGap) {
                Text("设置").font(DesignTokens.fontTitle)
                GroupBox("中枢地址") {
                    VStack(alignment: .leading, spacing: DesignTokens.settingsGap) {
                        HStack { TextField("中枢地址", text: $state.settingsHubURL).textFieldStyle(.roundedBorder); Button("保存") { state.saveHubURL() } }
                        Text(state.hasLocalCredentials ? "凭证已保存在本机配置，尚未迁入钥匙串。" : "未找到本机凭证。")
                            .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
                        if !state.settingsMessage.isEmpty { Text(state.settingsMessage).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText) }
                    }.frame(maxWidth: .infinity, alignment: .leading)
                }
                GroupBox("批准默认模式") {
                    Toggle("新任务默认需要批准", isOn: $state.approvalDefault).onChange(of: state.approvalDefault) { value in state.newNeedsApproval = value }
                        .frame(maxWidth: .infinity, alignment: .leading)
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
        }
    }
}

private struct OnboardingView: View {
    @ObservedObject var state: AppState
    var body: some View {
        VStack(alignment: .leading, spacing: DesignTokens.settingsGap) {
            Text("欢迎使用 Pack").font(DesignTokens.fontTitle)
            Text("1. 连接中枢：请先配置中枢地址。")
            Text("2. 确认身份：请安全配置本机凭证；当前版本尚未接入钥匙串。")
            Text("3. 配置完成后重新打开 App，进入主房间。")
            Button("知道了") { state.showOnboarding = false }
        }.padding(DesignTokens.settingsInset).frame(width: DesignTokens.memberLoginWidth)
    }
}

private struct MemberDirectory: View {
    @ObservedObject var state: AppState
    private func rowStatus(_ member: MemberInfo) -> String {
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
                    ForEach(state.members.sorted { a, b in
                        let aa = a.isOnline && !a.quotaExhausted; let bb = b.isOnline && !b.quotaExhausted
                        return aa == bb ? a.name < b.name : aa && !bb
                    }) { member in
                        Button { state.toggleMember(member.name) } label: {
                            HStack(spacing: DesignTokens.memberGap) {
                                MemberAvatarView(name: member.name, member: member, size: DesignTokens.memberListAvatar)
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

private struct MemberDetail: View {
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
                    MemberAvatarView(name: member.name, member: member, size: DesignTokens.memberAvatarLarge)
                    VStack(alignment: .leading) { Text(member.name).font(DesignTokens.fontTitle); Text(state.memberStatus(member)).font(DesignTokens.fontSmall) }
                    Spacer()
                    Button { state.selectedMemberName = nil } label: { Image(systemName: "xmark") }.buttonStyle(.plain).help("关闭成员详情")
                }
                Text("最近心跳：\(member.lastHeartbeat.map { Date(timeIntervalSince1970: $0).formatted() } ?? "暂无记录") · 本周已交活：暂无完整统计")
                    .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
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
                Text("已装技能：中枢未提供按成员查询接口").font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
                if member.role == "member" && member.name != "owner" {
                    Button("成员本人登录 / 重新登录") { state.showMemberLogin = true }.buttonStyle(.bordered)
                    Button("更换头像（接口待接入）") {}.disabled(true)
                    Button("移除成员") { state.showRemoveMember = true }.foregroundStyle(DesignTokens.danger)
                }
            }.padding(DesignTokens.memberInset)
        }.background(DesignTokens.bubbleAI)
            .task(id: member.name + ":" + recentTasks.map { "\($0.id)-\($0.updatedAt)" }.joined(separator: ",")) {
                await state.loadMemberRunDurations(member.name, taskIds: recentTasks.map(\.id))
            }
    }
}

private struct AddMemberView: View {
    @ObservedObject var state: AppState
    var body: some View {
        VStack(alignment: .leading, spacing: DesignTokens.memberGap) {
            Text("添加成员").font(DesignTokens.fontTitle)
            TextField("成员名字", text: $state.memberNameInput).textFieldStyle(.roundedBorder)
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

private struct MemberLoginShell: View {
    @ObservedObject var state: AppState
    var body: some View {
        VStack(spacing: DesignTokens.memberGap) {
            Text("成员本人登录").font(DesignTokens.fontTitle)
            Text("服务方登录页容器待接入。账号、密码和验证码应由成员本人在服务方页面输入。")
                .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
            RoundedRectangle(cornerRadius: DesignTokens.radiusCard).stroke(DesignTokens.hairline, lineWidth: DesignTokens.boardLine)
                .overlay(Text("登录页容器 · 尚未接入").font(DesignTokens.fontUI).foregroundStyle(DesignTokens.subText))
            Button("关闭") { state.showMemberLogin = false }
        }.padding(DesignTokens.chatInset).frame(width: DesignTokens.memberLoginWidth, height: DesignTokens.memberLoginHeight)
    }
}

private struct TaskDetail: View {
    @ObservedObject var state: AppState
    let task: PackTask
    @FocusState private var reasonFocused: Bool
    var body: some View {
        VStack(alignment: .leading, spacing: DesignTokens.detailSpacing) {
            HStack { Text("任务 #\(task.id) · \(task.title)").font(DesignTokens.fontTitle); Spacer(); Text(task.statusLabel).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText) }
            if !task.body.isEmpty { Text(task.body).font(DesignTokens.fontUI).textSelection(.enabled) }
            ChatTaskDetail(state: state, task: task)
            if let artifacts = task.envelope?.artifacts, !artifacts.isEmpty {
                ArtifactGroup(state: state, taskId: task.id, artifacts: artifacts)
            }
            if task.status == "review" {
                if state.reviewInputTaskId == task.id {
                    TextField("填写打回理由，回车提交", text: $state.reviewInput)
                        .focused($reasonFocused).onSubmit { state.reviewTask(task.id, approve: false, note: state.reviewInput) }
                        .onExitCommand { state.reviewInputTaskId = nil; state.reviewInput = "" }
                } else {
                    HStack {
                        Button("通过") { state.reviewTask(task.id, approve: true) }.buttonStyle(.borderedProminent).tint(DesignTokens.ink)
                        Button("打回") { state.reviewInput = ""; state.reviewInputTaskId = task.id; reasonFocused = true }.buttonStyle(.plain)
                    }.font(DesignTokens.fontSmall)
                }
            }
            if ["open", "claimed", "working"].contains(task.status) { Button("取消任务", role: .destructive) { state.cancelTask(task.id) }.buttonStyle(.bordered) }
        }.frame(maxWidth: .infinity, alignment: .leading).padding(DesignTokens.detailPadding).card()
    }
}

private struct CreateTaskView: View {
    @ObservedObject var state: AppState
    var body: some View {
        VStack(alignment: .leading, spacing: 15) {
            Text("派发新任务").font(.system(size: 19, weight: .semibold))
            Text("写清目标和要求，成员会在群里回报进展。").font(.system(size: 12)).foregroundStyle(.secondary)
            TextField("任务标题", text: $state.newTitle).textFieldStyle(.roundedBorder)
            TextField("任务要求和交付内容", text: $state.newBody, axis: .vertical).textFieldStyle(.roundedBorder).lineLimit(3...6)
            Picker("指派给", selection: $state.newAssignee) { ForEach(state.members.filter { $0.role == "member" }) { Text($0.name).tag($0.name) } }
            Toggle("需要我批准才能执行", isOn: $state.newNeedsApproval)
            HStack { Spacer(); Button("取消") { state.showCreateTask = false }; Button("建卡派活") { state.createTask() }.buttonStyle(.borderedProminent).disabled(state.newTitle.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty) }
        }.padding(24).frame(width: 470)
    }
}
