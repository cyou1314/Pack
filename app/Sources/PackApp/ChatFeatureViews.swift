import SwiftUI
import AppKit
import UniformTypeIdentifiers
import WebKit

struct RoomNameDialog: View {
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

struct CenterHeader: View {
    @ObservedObject var state: AppState
    var body: some View {
        HStack(spacing: DesignTokens.shellGap) {
            Text(state.selection == "tasks" ? "任务板" : state.selection == "members" ? "成员" : state.selection == "settings" ? "设置" : state.selection == "approvals" ? "收件箱" : state.currentRoom).font(DesignTokens.shellHeaderTitle)
            Spacer()
            if !state.pendingApprovals.isEmpty { Pill(title: "待批准 \(state.pendingApprovals.count)", color: PackStyle.amber) }
        }.padding(.horizontal, DesignTokens.shellHeaderInset).frame(height: DesignTokens.shellHeaderHeight)
    }
}

struct RoomView: View {
    @ObservedObject var state: AppState
    private var visibleMessages: [RoomMessage] { state.messages.filter { !["task_card", "report", "question_answer"].contains($0.kind) } }
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
                        .transition(.opacity.combined(with: .offset(y: DesignTokens.motionOffset)))
                        .overlay(RoundedRectangle(cornerRadius: DesignTokens.radiusCard).stroke(state.highlightedMessageId == message.id ? DesignTokens.blue : Color.clear, lineWidth: DesignTokens.boardHighlightLine).allowsHitTesting(false))
                        .id(message.id)
                }
                ForEach(state.failedMessagesByRoom[state.currentRoom] ?? []) { failed in
                    if state.sendingFailedID != failed.id {
                        FailedMessageRow(state: state, message: failed)
                    }
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
        }.onChange(of: state.highlightedMessageId) { _, id in
            if let id { withAnimation(DesignTokens.animStd) { proxy.scrollTo(id, anchor: .center) } }
        }
        }
        }
    }
}

struct FailedMessageRow: View {
    @ObservedObject var state: AppState
    let message: FailedChatMessage
    var body: some View {
        HStack {
            Spacer(minLength: 0)
            VStack(alignment: .trailing, spacing: DesignTokens.chatMetadataSpacing) {
                if let reply = message.reply { ReplyQuoteView(reply: reply) }
                if !message.body.isEmpty {
                    Text(message.body).font(DesignTokens.fontUI).foregroundStyle(.white)
                        .padding(.horizontal, DesignTokens.chatBubbleHorizontal).padding(.vertical, DesignTokens.chatBubbleVertical)
                        .background(DesignTokens.ink, in: RoundedRectangle(cornerRadius: DesignTokens.radiusBubble))
                        .textSelection(.enabled)
                }
                if !message.attachments.isEmpty {
                    HStack(spacing: DesignTokens.composerSpacing) {
                        ForEach(message.attachments) { item in
                            Group {
                                if let image = item.preview { Image(nsImage: image).resizable().scaledToFill() }
                                else { Image(systemName: "photo").foregroundStyle(DesignTokens.subText) }
                            }.frame(width: DesignTokens.attachmentChipSize, height: DesignTokens.attachmentChipSize)
                                .background(DesignTokens.bubbleAI).clipped().clipShape(RoundedRectangle(cornerRadius: DesignTokens.radiusSmall))
                        }
                    }
                }
                HStack(spacing: DesignTokens.messageActionSpacing) {
                    Button { state.retryFailedMessage(message) } label: {
                        Label("发送失败 · 点击重发", systemImage: "exclamationmark.circle.fill")
                    }.buttonStyle(.plain).foregroundStyle(DesignTokens.danger).disabled(state.sendingMessage != nil)
                    Button { state.discardFailedMessage(message) } label: { Image(systemName: "xmark") }
                        .buttonStyle(.plain).foregroundStyle(DesignTokens.subText).help("放弃并恢复草稿")
                }.font(DesignTokens.fontSmall)
                if let note = state.failedMessageNotices[message.id] {
                    Text(note).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.danger)
                }
            }.frame(maxWidth: DesignTokens.chatBubbleWidth, alignment: .trailing)
        }
    }
}

struct MessageOperations: View {
    @ObservedObject var state: AppState
    let message: RoomMessage
    private var usesBubble: Bool {
        if message.kind == "system" || message.kind == "question" { return false }
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

struct MessageActionButtons: View {
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
        }.font(DesignTokens.fontSmall).buttonStyle(.plain).transition(.opacity.combined(with: .offset(x: -DesignTokens.motionOffset)))
    }
}

struct MessageRow: View {
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
        } else if message.kind == "question" && !message.deleted {
            QuestionCard(state: state, message: message)
        } else if message.kind == "approval_request", let t = task {
            ApprovalCard(state: state, message: message, task: t)
        } else if message.kind == "task_card", let t = task {
            TaskCard(state: state, task: t)
        } else if message.kind == "report", let t = task, t.result != nil || t.envelope != nil {
            DeliveryCard(state: state, task: t)
        } else {
            HStack(alignment: .top, spacing: DesignTokens.chatRowSpacing) {
                if isOwner { Spacer(minLength: 0) } else { MemberAvatarView(name: message.author, member: member, size: DesignTokens.chatAvatar, showStatus: false).id(state.avatarRevision) }
                VStack(alignment: isOwner ? .trailing : .leading, spacing: DesignTokens.chatMetadataSpacing) {
                    if !isOwner { Text(message.author).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText) }
                    if let reply = decoded.reply { ReplyQuoteView(reply: reply) }
                    // 长消息折叠位：保留完整正文，待 B1-8 接入展开状态与阈值。
                    if !decoded.body.isEmpty || state.editingMessageId == message.id || message.deleted {
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
                    }
                    if !message.deleted && !message.attachments.isEmpty {
                        HStack(spacing: DesignTokens.messageActionSpacing) {
                            MessageAttachmentGroup(state: state, attachments: message.attachments)
                            if decoded.body.isEmpty && state.hoveredMessageId == message.id {
                                MessageActionButtons(state: state, message: message)
                            }
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
                            .contentTransition(.opacity).animation(DesignTokens.animFast, value: acknowledged)
                    }
                }.frame(maxWidth: DesignTokens.chatBubbleWidth, alignment: isOwner ? .trailing : .leading)
                if !isOwner { Spacer(minLength: 0) }
            }
        }
    }
}

struct MessageAttachmentGroup: View {
    @ObservedObject var state: AppState
    let attachments: [MessageAttachment]
    var body: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: DesignTokens.attachmentPlaceholderHeight, maximum: DesignTokens.attachmentImageMaxWidth), spacing: DesignTokens.detailSpacing)], spacing: DesignTokens.detailSpacing) {
            ForEach(attachments) { attachment in
                Group {
                    if let image = state.attachmentImages[attachment.id] {
                        Image(nsImage: image).resizable().scaledToFit()
                            .frame(maxWidth: DesignTokens.attachmentImageMaxWidth, maxHeight: DesignTokens.attachmentImageMaxHeight)
                            .clipShape(RoundedRectangle(cornerRadius: DesignTokens.radiusCard))
                            .accessibilityLabel(attachment.filename)
                    } else if state.attachmentFailures.contains(attachment.id) {
                        Button("图片加载失败 · 重试") { Task { await state.loadMessageAttachment(attachment.id, retry: true) } }
                            .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
                            .frame(maxWidth: .infinity, minHeight: DesignTokens.attachmentPlaceholderHeight)
                            .background(DesignTokens.bubbleAI, in: RoundedRectangle(cornerRadius: DesignTokens.radiusCard))
                    } else {
                        ProgressView().frame(maxWidth: .infinity, minHeight: DesignTokens.attachmentPlaceholderHeight)
                            .background(DesignTokens.bubbleAI, in: RoundedRectangle(cornerRadius: DesignTokens.radiusCard))
                    }
                }.task(id: "\(attachment.id)-\(state.attachmentImages[attachment.id] == nil)") { await state.loadMessageAttachment(attachment.id) }
            }
        }.frame(maxWidth: DesignTokens.attachmentImageMaxWidth, alignment: .leading)
    }
}

struct ReplyQuoteView: View {
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

struct Composer: View {
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
                                    MemberAvatarView(name: member.name, member: member, size: DesignTokens.mentionCandidateAvatar, showStatus: false).id(state.avatarRevision)
                                    Text(member.name).font(DesignTokens.fontUI)
                                    Spacer()
                                    Text(state.mentionStatus(member)).font(DesignTokens.fontSmall)
                                        .foregroundStyle(member.quotaExhausted ? DesignTokens.danger : DesignTokens.subText)
                                }.padding(.horizontal, DesignTokens.mentionCandidateHorizontal).padding(.vertical, DesignTokens.mentionCandidateVertical).contentShape(Rectangle())
                            }.buttonStyle(.plain)
                        }
                    }.background(Color(nsColor: .controlBackgroundColor))
                        .transition(.opacity.combined(with: .offset(y: DesignTokens.motionOffset)))
                }
                MentionTextEditor(state: state)
                if !state.attachments.isEmpty {
                    HStack(spacing: DesignTokens.composerSpacing) {
                        ForEach(state.attachments) { item in
                            VStack(spacing: DesignTokens.memberListTextSpacing) {
                                ZStack(alignment: .topTrailing) {
                                    Group {
                                        if let image = item.preview { Image(nsImage: image).resizable().scaledToFill() }
                                        else { Image(systemName: "photo").foregroundStyle(DesignTokens.subText) }
                                    }.frame(width: DesignTokens.attachmentChipSize, height: DesignTokens.attachmentChipSize)
                                        .background(DesignTokens.bubbleAI).clipped().clipShape(RoundedRectangle(cornerRadius: DesignTokens.radiusSmall))
                                    Button { state.removeAttachment(item.id) } label: { Image(systemName: "xmark.circle.fill") }
                                        .buttonStyle(.plain).frame(width: DesignTokens.attachmentChipButtonSize, height: DesignTokens.attachmentChipButtonSize)
                                        .disabled(state.sendingMessage != nil).help("移除图片")
                                }
                                if item.uploading { Text("上传中…").font(DesignTokens.fontSmall) }
                                else if item.error != nil { Button("上传失败 · 重试") { state.retryAttachment(item.id) }.font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.danger) }
                            }.help(item.error ?? item.url.lastPathComponent)
                        }
                    }
                }
            HStack(spacing: DesignTokens.composerSpacing) {
                Button { withAnimation(DesignTokens.animFast) { state.showAddMenu.toggle() } } label: {
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
                        .transition(.opacity.combined(with: .scale(scale: DesignTokens.menuEntranceScale, anchor: .bottomLeading)))
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
                    Toggle("加急", isOn: $state.newUrgent).toggleStyle(.switch).controlSize(.small)
                        .font(DesignTokens.fontSmall).disabled(state.inlineTaskSending)
                }
                Button { state.submitComposer() } label: {
                    Image(systemName: "arrow.up").font(DesignTokens.fontTitle).foregroundStyle(.white)
                        .frame(width: DesignTokens.composerSend, height: DesignTokens.composerSend)
                        .background(DesignTokens.blue, in: Circle())
                }.buttonStyle(.plain)
                    .disabled(state.sendingMessage != nil || (state.showInlineTask && (state.inlineTaskSending || state.draftMessage.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !state.attachments.isEmpty)))
                    .help(state.showInlineTask ? "建卡派活" : "发送")
            }
            if !state.inlineTaskError.isEmpty {
                Text(state.inlineTaskError).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.danger)
            }
            if let notice = state.attachmentNoticeByRoom[state.currentRoom] {
                Text(notice).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.danger)
            }
            if state.showInlineTask && !state.attachments.isEmpty {
                Text("派活暂不支持图片，请先移除图片或关闭派活。")
                    .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.danger)
            }
        }.animation(DesignTokens.animFast, value: state.mentionPopupVisible)
            .fixedSize(horizontal: false, vertical: true)
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
            .background(state.hoveredMenuItem == title ? DesignTokens.blue.opacity(DesignTokens.shellSelectionOpacity) : Color.clear, in: RoundedRectangle(cornerRadius: DesignTokens.radiusSmall))
            .onHover { inside in state.hoveredMenuItem = inside ? title : nil }
    }
    private func chooseFiles(imagesOnly: Bool) {
        let picker = NSOpenPanel()
        picker.allowsMultipleSelection = true
        picker.canChooseDirectories = false
        if imagesOnly { picker.allowedContentTypes = [.image] }
        picker.begin { result in if result == .OK { state.addAttachments(picker.urls) } }
    }
}

