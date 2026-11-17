import SwiftUI

private final class SearchPanelModel: ObservableObject {
    @Published var query = ""
    @Published var selectedIndex = 0
}

private enum SearchResult: Identifiable {
    case room(RoomItem), task(PackTask), member(MemberInfo), message(RoomMessage)
    var id: String {
        switch self {
        case .room(let room): return "room:\(room.id)"
        case .task(let task): return "task:\(task.id)"
        case .member(let member): return "member:\(member.name)"
        case .message(let message): return "message:\(message.id)"
        }
    }
}

struct SearchPanel: View {
    @ObservedObject var state: AppState
    @StateObject private var model = SearchPanelModel()
    @FocusState private var searchFocused: Bool

    private var query: String { model.query.trimmingCharacters(in: .whitespacesAndNewlines) }
    private func matches(_ value: String) -> Bool { value.localizedCaseInsensitiveContains(query) }
    private var groups: [(String, [SearchResult])] {
        guard !query.isEmpty else { return [] }
        return [
            ("房间", state.rooms.filter { matches($0.name) }.map { .room($0) }),
            ("任务 · 当前房间已加载", state.tasks.filter { matches($0.title) || matches($0.body) || matches("#\($0.id)") }.map { .task($0) }),
            ("成员", state.members.filter { matches($0.name) }.map { .member($0) }),
            ("消息 · 当前房间已加载", state.messages.filter { !["task_card", "report"].contains($0.kind) && !$0.deleted && matches(ReplyCodec.decode($0.body).body) }.map { .message($0) })
        ]
    }
    private var results: [SearchResult] { groups.flatMap { $0.1 } }

    var body: some View {
        VStack(alignment: .leading, spacing: DesignTokens.searchGap) {
            HStack {
                Image(systemName: "magnifyingglass").foregroundStyle(DesignTokens.subText)
                TextField("搜索房间、任务、成员或消息", text: $model.query)
                    .textFieldStyle(.plain).font(DesignTokens.fontUI).focused($searchFocused)
                    .onSubmit { openSelected() }
                    .onKeyPress(.downArrow) { moveSelection(1); return .handled }
                    .onKeyPress(.upArrow) { moveSelection(-1); return .handled }
                Button("关闭") { state.showSearch = false }.buttonStyle(.plain).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
            }.padding(DesignTokens.searchRowInset)
                .overlay(RoundedRectangle(cornerRadius: DesignTokens.radiusCard).stroke(DesignTokens.hairline, lineWidth: DesignTokens.boardLine))
            Text("任务与消息仅搜索当前房间已加载内容；房间和成员搜索已加载列表。")
                .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: DesignTokens.searchGap) {
                        if query.isEmpty { Text("输入关键词开始搜索").font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText) }
                        else if results.isEmpty { Text("没有匹配结果").font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText) }
                        ForEach(groups, id: \.0) { title, items in
                            if !items.isEmpty {
                                Text(title).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
                                ForEach(items) { item in
                                    let index = results.firstIndex { $0.id == item.id } ?? 0
                                    Button { open(item) } label: { resultRow(item) }
                                        .buttonStyle(.plain).padding(DesignTokens.searchRowInset)
                                        .frame(maxWidth: .infinity, alignment: .leading)
                                        .background(index == model.selectedIndex ? DesignTokens.blue.opacity(DesignTokens.searchHighlightOpacity) : Color.clear, in: RoundedRectangle(cornerRadius: DesignTokens.radiusCard))
                                        .id(item.id)
                                }
                            }
                        }
                    }.frame(maxWidth: .infinity, alignment: .leading)
                }.onChange(of: model.selectedIndex) { index in
                    if results.indices.contains(index) { proxy.scrollTo(results[index].id, anchor: .center) }
                }
            }
            Text("↑↓ 选择 · 回车打开 · Esc 关闭").font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
        }.padding(DesignTokens.searchInset).frame(width: DesignTokens.searchWidth, height: DesignTokens.searchHeight)
            .onAppear { searchFocused = true }
            .onChange(of: model.query) { _ in model.selectedIndex = 0 }
            .onMoveCommand { direction in
                guard !results.isEmpty else { return }
                if direction == .down { moveSelection(1) }
                if direction == .up { moveSelection(-1) }
            }
            .onExitCommand { state.showSearch = false }
    }

    @ViewBuilder private func resultRow(_ result: SearchResult) -> some View {
        switch result {
        case .room(let room): highlighted(room.name)
        case .task(let task):
            VStack(alignment: .leading, spacing: DesignTokens.searchGap) {
                highlighted("#\(task.id) \(task.title)")
                Text("\(state.currentRoom) · \(task.assignee ?? "未指派") · \(task.statusLabel)").font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
            }
        case .member(let member):
            HStack { highlighted(member.name); Spacer(); Text(state.memberStatus(member)).font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText) }
        case .message(let message):
            VStack(alignment: .leading, spacing: DesignTokens.searchGap) {
                Text("\(state.currentRoom) · \(message.author) · \(Date(timeIntervalSince1970: message.createdAt).formatted(date: .abbreviated, time: .shortened))")
                    .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
                highlighted(snippet(ReplyCodec.decode(message.body).body)).lineLimit(DesignTokens.searchSnippetLines)
            }
        }
    }
    private func snippet(_ value: String) -> String {
        let singleLine = value.replacingOccurrences(of: "\n", with: " ")
        guard let match = singleLine.range(of: query, options: [.caseInsensitive, .diacriticInsensitive]) else { return singleLine }
        let start = singleLine.index(match.lowerBound, offsetBy: -DesignTokens.searchSnippetContext, limitedBy: singleLine.startIndex) ?? singleLine.startIndex
        let end = singleLine.index(match.upperBound, offsetBy: DesignTokens.searchSnippetContext, limitedBy: singleLine.endIndex) ?? singleLine.endIndex
        return (start == singleLine.startIndex ? "" : "…") + String(singleLine[start..<end]) + (end == singleLine.endIndex ? "" : "…")
    }
    private func moveSelection(_ delta: Int) {
        guard !results.isEmpty else { return }
        model.selectedIndex = max(0, min(model.selectedIndex + delta, results.count - 1))
    }
    private func highlighted(_ value: String) -> Text {
        guard !query.isEmpty, let match = value.range(of: query, options: [.caseInsensitive, .diacriticInsensitive]) else { return Text(value).font(DesignTokens.fontUI) }
        return (Text(String(value[..<match.lowerBound])) + Text(String(value[match])).foregroundColor(DesignTokens.blue).bold() + Text(String(value[match.upperBound...]))).font(DesignTokens.fontUI)
    }
    private func openSelected() {
        guard results.indices.contains(model.selectedIndex) else { return }
        open(results[model.selectedIndex])
    }
    private func open(_ result: SearchResult) {
        state.showSearch = false
        switch result {
        case .room(let room): state.selectRoom(room.name)
        case .task(let task): state.openTaskDetail(task.id)
        case .member(let member): state.selectedMemberName = member.name; state.selection = "members"
        case .message(let message):
            state.selection = "room"
            state.highlightedMessageId = nil
            DispatchQueue.main.async {
                state.highlightedMessageId = message.id
                Task {
                    try? await Task.sleep(nanoseconds: DesignTokens.searchFlashNanoseconds)
                    if state.highlightedMessageId == message.id { state.highlightedMessageId = nil }
                }
            }
        }
    }
}
