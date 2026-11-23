import Foundation
import SwiftUI
import AppKit
import PackCore

@MainActor
extension AppState {
    // MARK: - Chat, composer and attachments

    /// Range of the trailing token of the draft when it is an unfinished
    /// mention: the token starts at the beginning of the text or right
    /// after a space/newline and its first character is @ or ＠.
    /// (v1 assumes the cursor is at the end of the text while typing.)
    private var mentionTokenRange: Range<String.Index>? {
        let text = draftMessage
        guard !text.isEmpty else { return nil }
        var start = text.startIndex
        if let ws = text.lastIndex(where: { $0 == " " || $0 == "\n" }) {
            start = text.index(after: ws)
        }
        guard start < text.endIndex else { return nil }
        let first = text[start]
        guard first == "@" || first == "＠" else { return nil }
        return start..<text.endIndex
    }

    /// The text after the @ of the unfinished mention, nil when the draft
    /// does not end in a mention token.
    var mentionQuery: String? {
        guard let r = mentionTokenRange else { return nil }
        return String(draftMessage[r].dropFirst())
    }

    /// Members the popup offers: assignable members (same role filter as
    /// the 派活 picker) minus the App's own chat identity "owner",
    /// prefix-filtered by the query, case-insensitive.
    var mentionCandidates: [MemberInfo] {
        guard let q = mentionQuery?.lowercased() else { return [] }
        return members.filter { m in
            m.role == "member" && m.name != "owner" &&
            (q.isEmpty || m.name.lowercased().hasPrefix(q))
        }.sorted { a, b in
            let aAvailable = a.isOnline && !a.quotaExhausted
            let bAvailable = b.isOnline && !b.quotaExhausted
            return aAvailable == bAvailable ? a.name < b.name : aAvailable && !bAvailable
        }
    }

    func mentionStatus(_ member: MemberInfo) -> String {
        if member.quotaExhausted { return "额度耗尽" }
        if !member.isOnline { return pendingMembers.contains(member.name) && member.lastHeartbeat == nil ? "接入中" : "离线" }
        return memberStatus(member)
    }

    var mentionPopupVisible: Bool {
        mentionQuery != nil && !mentionCandidates.isEmpty &&
        draftMessage != mentionDismissedText
    }

    func questionAnswer(for question: RoomMessage) -> (author: String, body: String)? {
        if let answer = messages.first(where: { $0.kind == "question_answer" && !$0.deleted && $0.replyToId == question.id }) {
            return (answer.author, answer.body)
        }
        return localQuestionAnswers[question.id]
    }

    var questionLinksUnavailable: Bool {
        messages.contains { $0.kind == "question_answer" && !$0.deleted && $0.replyToId == nil }
    }

    func answerQuestion(_ question: RoomMessage, body: String) {
        guard ["owner", "dispatcher"].contains(questionResponderName ?? ""),
              !body.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              questionAnswer(for: question) == nil, !questionLinksUnavailable,
              !questionSending.contains(question.id), let answerClient = ownerClient ?? client else { return }
        let room = currentRoom
        let author = questionResponderName ?? ""
        questionSending.insert(question.id)
        questionErrors[question.id] = nil
        Task {
            defer { questionSending.remove(question.id) }
            do {
                _ = try await answerClient.call(name: "room_post", args: ["room": room, "kind": "question_answer", "body": body, "reply_to_id": question.id])
                localQuestionAnswers[question.id] = (author: author, body: body)
                questionInputs[question.id] = nil
                if currentRoom == room { await refresh() }
            } catch {
                questionErrors[question.id] = "答复未确认，请检查网络及提问状态。"
                if currentRoom == room { await refresh() }
            }
        }
    }

    func addAttachments(_ urls: [URL]) {
        let room = currentRoom
        guard let ownerClient else { attachmentNoticeByRoom[room] = "缺少 owner 凭证，无法上传图片。"; return }
        let available = max(0, AttachmentLimits.maxCount - (attachmentsByRoom[room]?.count ?? 0))
        guard available > 0 else { attachmentNoticeByRoom[room] = "每条消息最多添加 4 张图片。"; return }
        attachmentNoticeByRoom[room] = urls.count > available ? "每条消息最多添加 4 张图片，多余图片未添加。" : nil
        for url in urls.prefix(available) {
            let item = PendingAttachment(id: UUID(), url: url)
            attachmentsByRoom[room, default: []].append(item)
            uploadAttachment(item.id, room: room, client: ownerClient)
        }
    }

    func removeAttachment(_ id: UUID) {
        attachmentsByRoom[currentRoom]?.removeAll { $0.id == id }
    }

    func retryAttachment(_ id: UUID) {
        guard let ownerClient else { attachmentNoticeByRoom[currentRoom] = "缺少 owner 凭证，无法上传图片。"; return }
        let room = currentRoom
        guard let index = attachmentsByRoom[room]?.firstIndex(where: { $0.id == id }) else { return }
        attachmentsByRoom[room]?[index].uploading = true
        attachmentsByRoom[room]?[index].error = nil
        uploadAttachment(id, room: room, client: ownerClient)
    }

    private func uploadAttachment(_ id: UUID, room: String, client: HubClient) {
        guard let url = attachmentsByRoom[room]?.first(where: { $0.id == id })?.url else { return }
        Task {
            do {
                let prepared = try await Task.detached { try AttachmentImage.prepare(url) }.value
                guard let previewIndex = attachmentsByRoom[room]?.firstIndex(where: { $0.id == id }) else { return }
                attachmentsByRoom[room]?[previewIndex].preview = NSImage(data: prepared.data)
                let result = try await client.call(name: "attachment_put", args: ["room": room, "filename": prepared.filename,
                    "mime": prepared.mime, "data_base64": prepared.data.base64EncodedString()])
                guard let attachmentID = optIntVal(result["attachment_id"]), attachmentID > 0 else { throw HubError.badResponse("上传结果缺少附件编号") }
                guard let index = attachmentsByRoom[room]?.firstIndex(where: { $0.id == id }) else { return }
                attachmentsByRoom[room]?[index].uploadedID = attachmentID
                attachmentsByRoom[room]?[index].preview = NSImage(data: prepared.data)
                attachmentsByRoom[room]?[index].uploading = false
            } catch {
                guard let index = attachmentsByRoom[room]?.firstIndex(where: { $0.id == id }) else { return }
                attachmentsByRoom[room]?[index].uploading = false
                if let error = error as? HubError {
                    switch error { case .badResponse(let message), .toolError(let message): attachmentsByRoom[room]?[index].error = message }
                } else { attachmentsByRoom[room]?[index].error = error.localizedDescription }
            }
        }
    }

    func loadMessageAttachment(_ id: Int, retry: Bool = false) async {
        if retry { attachmentFailures.remove(id) }
        guard attachmentImages[id] == nil, !attachmentFailures.contains(id), !attachmentLoading.contains(id) else { return }
        guard let client else { attachmentFailures.insert(id); return }
        attachmentLoading.insert(id)
        defer { attachmentLoading.remove(id) }
        do {
            let result = try await client.call(name: "attachment_get", args: ["attachment_id": id])
            guard let encoded = result["data_base64"] as? String, let data = Data(base64Encoded: encoded),
                  data.count <= AttachmentLimits.maxBytes, let image = NSImage(data: data) else { throw HubError.badResponse("图片数据不可用") }
            if attachmentImages.count >= AttachmentLimits.imageCacheCount, let key = attachmentImages.keys.first { attachmentImages.removeValue(forKey: key) }
            attachmentImages[id] = image
        } catch { attachmentFailures.insert(id) }
    }

    func sendDraft() {
        let room = currentRoom
        let selected = attachmentsByRoom[room] ?? []
        guard sendingMessage == nil else { return }
        guard selected.allSatisfy({ !$0.uploading && $0.uploadedID != nil }) else {
            attachmentNoticeByRoom[room] = "请等待图片上传，或移除／重试上传失败的图片。"; return
        }
        let body = draftMessage.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !body.isEmpty || !selected.isEmpty else { return }
        guard let ownerClient else { lastActionNote = "操作失败：缺少 owner 凭证"; return }
        let reply = replyByRoom.removeValue(forKey: room)
        let outgoing = FailedChatMessage(id: UUID(), room: room, body: body,
                                         wireBody: ReplyCodec.encode(body, reply: reply),
                                         reply: reply, attachments: selected)
        let selectedIDs = Set(selected.map(\.id))
        attachmentsByRoom[room]?.removeAll { selectedIDs.contains($0.id) }
        attachmentNoticeByRoom[room] = nil
        draftMessage = ""
        sendChat(outgoing, client: ownerClient, retry: false)
    }

    func retryFailedMessage(_ message: FailedChatMessage) {
        guard sendingMessage == nil, let ownerClient,
              failedMessagesByRoom[message.room]?.contains(where: { $0.id == message.id }) == true else { return }
        failedMessageNotices[message.id] = nil
        sendChat(message, client: ownerClient, retry: true)
    }

    private func sendChat(_ message: FailedChatMessage, client: HubClient, retry: Bool) {
        sendingRoom = message.room
        sendingMessage = message.body
        sendingReply = message.reply
        sendingFailedID = retry ? message.id : nil
        Task {
            do {
                await client.discoverBeforeSending()
                var args: [String: Any] = ["room": message.room, "body": message.wireBody, "kind": "chat"]
                let ids = message.attachments.compactMap(\.uploadedID)
                if !ids.isEmpty {
                    let json = try JSONSerialization.data(withJSONObject: ids)
                    args["attachment_ids"] = String(decoding: json, as: UTF8.self)
                }
                _ = try await client.call(name: "room_post", args: args)
                failedMessagesByRoom[message.room]?.removeAll { $0.id == message.id }
                failedMessageNotices[message.id] = nil
                sendingReply = nil; sendingMessage = nil; sendingRoom = nil; sendingFailedID = nil
                lastActionNote = "已发送"
                await refresh()
            } catch {
                if failedMessagesByRoom[message.room]?.contains(where: { $0.id == message.id }) != true {
                    failedMessagesByRoom[message.room, default: []].append(message)
                }
                sendingReply = nil; sendingMessage = nil; sendingRoom = nil; sendingFailedID = nil
                lastActionNote = "操作未完成，请检查连接后重试"
            }
        }
    }

    func discardFailedMessage(_ message: FailedChatMessage) {
        guard sendingFailedID != message.id,
              failedMessagesByRoom[message.room]?.contains(where: { $0.id == message.id }) == true else { return }
        let existingReply = replyByRoom[message.room]
        if let reply = message.reply, let existingReply, existingReply != reply {
            failedMessageNotices[message.id] = "当前草稿已有其他引用，请先处理引用再放弃此消息。"; return
        }
        let existingAttachments = attachmentsByRoom[message.room] ?? []
        guard existingAttachments.count + message.attachments.count <= AttachmentLimits.maxCount else {
            failedMessageNotices[message.id] = "当前草稿图片数量已满，请先移除图片再放弃此消息。"; return
        }
        let existingText = message.room == currentRoom ? draftMessage : (roomDrafts[message.room] ?? "")
        let restored = [existingText, message.body].filter { !$0.isEmpty }.joined(separator: "\n")
        if message.room == currentRoom { draftMessage = restored }
        else { roomDrafts[message.room] = restored; UserDefaults.standard.set(roomDrafts, forKey: "pack.roomDrafts") }
        if let reply = message.reply, existingReply == nil { replyByRoom[message.room] = reply }
        attachmentsByRoom[message.room] = existingAttachments + message.attachments
        failedMessagesByRoom[message.room]?.removeAll { $0.id == message.id }
        failedMessageNotices[message.id] = nil
    }

    func replyTo(_ message: RoomMessage) {
        let visible = message.deleted ? "（已删除）" : ReplyCodec.decode(message.body).body
        let snapshot = String(visible.prefix(DesignTokens.replySnapshotLimit))
        replyByRoom[currentRoom] = ReplySnapshot(author: message.author, text: snapshot)
        selection = "room"
        composerFocusRequest += 1
    }

    func editMessage(_ message: RoomMessage) {
        guard message.author == "owner", message.kind == "chat", !message.deleted else { return }
        editingMessageId = message.id
        editingMessageText = ReplyCodec.decode(message.body).body
    }

    func saveMessageEdit(_ message: RoomMessage) {
        let text = editingMessageText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard message.author == "owner", message.kind == "chat", !message.deleted, !text.isEmpty, let ownerClient else { return }
        let wireText = ReplyCodec.encode(text, reply: ReplyCodec.decode(message.body).reply)
        Task {
            do {
                _ = try await ownerClient.call(name: "message_edit", args: ["message_id": message.id, "new_body": wireText])
                editingMessageId = nil; editingMessageText = ""
                await refresh()
            } catch { lastActionNote = "操作未完成，请检查连接后重试" }
        }
    }

    func deleteMessage(_ message: RoomMessage) {
        guard message.author == "owner", message.kind == "chat", !message.deleted, let ownerClient else { return }
        Task {
            do {
                _ = try await ownerClient.call(name: "message_delete", args: ["message_id": message.id])
                await refresh()
            } catch { lastActionNote = "操作未完成，请检查连接后重试" }
        }
    }

    /// Replace the unfinished @… token with "@成员名 " — always the
    /// half-width @ and the member's canonical hub name, because the
    /// room watcher turns plain-text @全名 into task cards.
    func completeMention(_ member: MemberInfo) {
        guard let r = mentionTokenRange else { return }
        draftMessage.replaceSubrange(r, with: "@\(member.name) ")
        mentionDismissedText = nil
        composerFocusRequest += 1
    }

    func dismissMentions() {
        mentionDismissedText = draftMessage
    }

    func submitComposer() {
        if showInlineTask {
            guard replyByRoom[currentRoom] == nil else { inlineTaskError = "回复消息请先关闭派活"; return }
            createTaskFromDraft()
        } else { sendDraft() }
    }

}
